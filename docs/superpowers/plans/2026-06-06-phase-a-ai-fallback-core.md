# Phase A: AI 兜底核心 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当告警未命中任何 Runbook 时，Agent 调用 LLM 自主分析可观测数据，生成结构化处置方案并校验风险，代替原有的"未匹配到 Runbook"空返回。

**Architecture:** 新增 `agent/agents/fallback.py` 作为兜底 Agent，改造 `rca.py` 的 `_build_action_plan()` 使其在 Runbook 未命中时异步调用 Fallback Agent。AI 生成的方案复用现有的 `risk.py` 做白名单校验和风险评分，以相同的数据结构（`runbook` dict + `risk_assessment` dict）流向下游，对飞书卡片和审批流透明。

**Tech Stack:** Python 3.12+, DeepSeek LLM (via `agent/llm/client.py` `chat_json`), 现有 ActionStep / evaluate_risk 复用

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `agent/agents/fallback.py` | **新增** | LLM 自主方案生成的 system prompt + `generate_ai_action_plan()` 函数 |
| `agent/agents/rca.py` | **修改** | `_build_action_plan()` 改为 async，Runbook 未命中时调用 fallback |
| `agent/agents/risk.py` | **修改** | `evaluate_risk()` 接受 AI 生成的步骤（已经是 ActionStep，无需改动）；新增 AI 方案标记注入 |
| `tests/test_fallback.py` | **新增** | Fallback Agent 单元测试 |

**不涉及的文件（留给后续 Phase）：**
- `agent/workflows/alert_workflow.py` — Phase C 才改造
- `agent/agents/executor.py` — Phase C 才改造
- `agent/agents/verify.py` — Phase C 才改造
- `agent/templates/cards/` — Phase B 新增卡片
- `agent/db/models.py` — Phase D 新增字段

---

## 接口约定

### `generate_ai_action_plan()` 返回值

```python
# 成功时返回：
{
    "name": "ai_fallback",                    # 固定值，标识 AI 兜底方案
    "steps": [                                # ActionStep.to_dict() 列表
        {"risk_level": "低风险", "description": "...", "command": "kubectl scale ..."},
    ],
    "ai_generated": True,                     # 标记，下游可据此展示 AI 标识
    "ai_reasoning": "根据 CPU 和 QPS 数据...",  # LLM 推理过程原文
    "verification": {                         # AI 建议的验证条件
        "metric": "cpu",
        "operator": "<",
        "threshold": 70.0,
        "description": "CPU 使用率降至 70% 以下"
    },
    "confidence": 0.75,
}

# 失败时返回 None（LLM 调用异常或输出不合法）
```

### 与现有系统的对接点

`_build_action_plan()` 返回值和现在完全一样：`tuple[dict | None, dict | None]`。
区别是 `dict["name"]` 为 `"ai_fallback"` 且多了 `ai_generated`、`ai_reasoning`、`verification`、`confidence` 字段。
下游 `_save_diagnosis()` 和 `_notify_diagnosis()` 通过 `runbook.get("steps")` 取步骤，不感知差异。

risk_assessment 的 `warnings` 中追加 `"⚠️ AI 自主生成方案，未匹配预置 Runbook，请仔细确认"`，`factors` 中追加 `"AI 兜底方案，置信度: 0.75"`。

---

### Task 1: 编写 Fallback Agent 核心（`agent/agents/fallback.py`）

**Files:**
- Create: `agent/agents/fallback.py`
- Test: `tests/test_fallback.py`

- [ ] **Step 1: 编写 Fallback Agent 文件**

```python
# agent/agents/fallback.py
import json
import logging

from agent.agents.runbook import ActionStep
from agent.llm.client import chat_json

logger = logging.getLogger("ops-agent.fallback")

FALLBACK_SYSTEM_PROMPT = """你是一个 SRE 运维专家。当前告警未匹配到任何预置 Runbook，你需要**自主分析可观测数据并制定处置方案**。

## 分析原则

1. 综合 CPU、内存、QPS、响应时间、错误率等指标，结合 Pod 状态和错误日志，判断根因
2. 优先选择**可逆操作**（扩容、重启 Pod、回滚部署），严禁不可逆操作（删库、删 PVC、删 ConfigMap）
3. 命令**必须**是 kubectl 命令，且以以下前缀之一开头（白名单约束）：
   - kubectl scale deployment
   - kubectl delete pod
   - kubectl rollout undo deployment
   - kubectl set resources deployment
   - kubectl get pods
   - kubectl describe pod
4. 每步标注风险等级：[低风险] / [中风险] / [高风险]。高风险步骤不会自动执行，仅作建议
5. 指定验证条件：执行后检查哪个指标 + 判断符（< 或 >）+ 阈值 + 文字说明

## 输出格式

严格输出以下 JSON，不要 markdown 包裹，不要额外解释：

{
  "reasoning": "推理过程（200字以内，说明为什么选择这个方案）",
  "steps": [
    {"risk_level": "低风险", "description": "步骤描述", "command": "kubectl get pods -n demo"}
  ],
  "rollback": "kubectl rollout undo deployment xxx -n demo",
  "verification": {
    "metric": "cpu",
    "operator": "<",
    "threshold": 70.0,
    "description": "CPU 使用率降至 70% 以下"
  },
  "confidence": 0.75
}

## 验证指标的合法取值

metric 只能取: cpu, memory, qps, rt_avg, error_rate
operator 只能取: < 或 >
threshold: cpu 取 0-100 的数字（百分比），memory 取 0-1 的小数（比率），qps 取正数，rt_avg 取正数（秒），error_rate 取 0-1 的小数（比率）"""


def _build_fallback_prompt(context: dict, alert: dict, diagnosis: dict) -> str:
    """构建 Fallback Agent 的用户提示词"""
    metrics = context.get("metrics", {})
    logs = context.get("logs", [])
    pods = context.get("pods", {})
    cmdb = context.get("cmdb", {})

    cpu = metrics.get("cpu", {})
    memory = metrics.get("memory", {})
    qps = metrics.get("qps", {})
    rt = metrics.get("rt_avg", {})
    error_rate = metrics.get("error_rate", {})

    log_lines = [l.get("line", "") for l in logs[:20]]
    log_text = "\n".join(f"  - {line}" for line in log_lines) if log_lines else "  无错误日志"

    return f"""请根据以下可观测数据，自主分析并制定处置方案。

=== 告警信息 ===
- 告警名称: {alert.get('alertname', '未知')}
- 服务: {alert.get('service', '未知')}
- 环境: {alert.get('env', 'prod')}
- 级别: {alert.get('severity', 'P3')}
- 告警值: {alert.get('value', '无')}

=== LLM 初步诊断 ===
- 根因: {diagnosis.get('root_cause', '未知')}

=== 指标数据 ===
- CPU使用率: {cpu.get('current', 0):.1f}%
- 内存使用: {memory.get('current', 0):.0f} bytes
- QPS: {qps.get('current', 0):.1f} req/s
- 平均响应时间: {rt.get('current', 0):.4f}s
- 错误率: {error_rate.get('current', 0):.4f}

=== Pod 状态 ===
- 总数: {pods.get('total', 0)}
- 就绪: {pods.get('ready', 0)}
- Pod 明细: {json.dumps(pods.get('pods', []), ensure_ascii=False) if pods.get('pods') else '无'}

=== 最近错误日志 ===
{log_text}

=== CMDB 信息 ===
- 负责人: {cmdb.get('owner', '未知')}
- 团队: {cmdb.get('team', '未知')}
- 依赖服务: {', '.join(cmdb.get('dependencies', [])) or '无'}"""


def _validate_ai_output(result: dict) -> list[str]:
    """校验 LLM 输出的合法性，返回错误列表（空列表表示通过）"""
    errors = []

    if not isinstance(result.get("steps"), list) or len(result["steps"]) == 0:
        errors.append("steps 为空或不是列表")
        return errors

    valid_metrics = {"cpu", "memory", "qps", "rt_avg", "error_rate"}
    verification = result.get("verification", {})
    metric = verification.get("metric", "")
    operator = verification.get("operator", "")
    threshold = verification.get("threshold")

    if metric not in valid_metrics:
        errors.append(f"验证指标 {metric} 不在合法范围 {valid_metrics}")
    if operator not in ("<", ">"):
        errors.append(f"验证操作符 {operator} 不是 < 或 >")
    if not isinstance(threshold, (int, float)):
        errors.append(f"验证阈值 {threshold} 不是数字")

    whitelist_prefixes = (
        "kubectl scale deployment",
        "kubectl delete pod",
        "kubectl rollout undo deployment",
        "kubectl set resources deployment",
        "kubectl get pods",
        "kubectl describe pod",
    )
    valid_risk_levels = {"低风险", "中风险", "高风险"}

    for i, step in enumerate(result["steps"]):
        command = (step.get("command") or "").strip()
        if not command:
            errors.append(f"步骤 {i+1}: 缺少 command")
        elif not any(command.startswith(prefix) for prefix in whitelist_prefixes):
            errors.append(f"步骤 {i+1}: 命令 '{command[:60]}' 不在白名单中")
        if step.get("risk_level") not in valid_risk_levels:
            errors.append(f"步骤 {i+1}: 风险等级 '{step.get('risk_level')}' 不合法（应为 低风险/中风险/高风险）")
        if not step.get("description"):
            errors.append(f"步骤 {i+1}: 缺少 description")

    return errors


async def generate_ai_action_plan(
    context: dict,
    alert: dict,
    diagnosis: dict,
) -> dict | None:
    """调用 LLM 自主生成处置方案

    Returns:
        AI 生成的 runbook dict（含 ai_generated 标记），LLM 异常或输出非法时返回 None
    """
    alert_name = alert.get("alertname", "未知")
    service = alert.get("service", "未知")
    logger.info(
        "进入 Fallback Agent: alert=%s, service=%s, has_context=%s, has_diagnosis=%s",
        alert_name,
        service,
        bool(context),
        bool(diagnosis),
    )

    try:
        prompt = _build_fallback_prompt(context, alert, diagnosis)
        logger.info("调用 LLM 自主生成方案: alert=%s, prompt_length=%s", alert_name, len(prompt))
        result = await chat_json(prompt, system=FALLBACK_SYSTEM_PROMPT)
    except Exception as exc:
        logger.error("Fallback Agent LLM 调用失败: alert=%s, error=%s", alert_name, exc)
        return None

    errors = _validate_ai_output(result)
    if errors:
        logger.warning(
            "Fallback Agent LLM 输出校验失败: alert=%s, errors=%s",
            alert_name,
            errors,
        )
        return None

    reasoning = result.get("reasoning", "")
    confidence = float(result.get("confidence", 0.5))
    logger.info(
        "Fallback Agent 方案生成完成: alert=%s, steps=%s, confidence=%s, reasoning=%s",
        alert_name,
        len(result["steps"]),
        confidence,
        reasoning[:80],
    )

    steps = [
        {
            "risk_level": step["risk_level"],
            "description": step["description"],
            "command": step.get("command", ""),
        }
        for step in result["steps"]
    ]

    return {
        "name": "ai_fallback",
        "steps": steps,
        "ai_generated": True,
        "ai_reasoning": reasoning,
        "verification": result.get("verification", {}),
        "confidence": confidence,
    }
```

- [ ] **Step 2: 编写单元测试（`tests/test_fallback.py`）**

```python
# tests/test_fallback.py
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from agent.agents.fallback import (
    _validate_ai_output,
    _build_fallback_prompt,
    FALLBACK_SYSTEM_PROMPT,
)


class ValidateAiOutputTest(TestCase):
    def test_valid_output_passes_all_checks(self):
        result = {
            "reasoning": "流量上涨导致 CPU 高，扩容可缓解",
            "steps": [
                {
                    "risk_level": "低风险",
                    "description": "查看当前 Pod 状态",
                    "command": "kubectl get pods -n demo",
                },
                {
                    "risk_level": "中风险",
                    "description": "扩容 order-service 到 4 副本",
                    "command": "kubectl scale deployment order-service -n demo --replicas=4",
                },
            ],
            "rollback": "kubectl rollout undo deployment order-service -n demo",
            "verification": {
                "metric": "cpu",
                "operator": "<",
                "threshold": 70.0,
                "description": "CPU 降至 70% 以下",
            },
            "confidence": 0.8,
        }

        errors = _validate_ai_output(result)
        self.assertEqual(errors, [])

    def test_rejects_empty_steps(self):
        errors = _validate_ai_output({"steps": [], "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0}})
        self.assertGreater(len(errors), 0)
        self.assertIn("steps 为空或不是列表", errors)

    def test_rejects_missing_steps(self):
        errors = _validate_ai_output({"verification": {"metric": "cpu", "operator": "<", "threshold": 70.0}})
        self.assertGreater(len(errors), 0)

    def test_rejects_non_whitelist_command(self):
        result = {
            "steps": [
                {
                    "risk_level": "低风险",
                    "description": "执行危险脚本",
                    "command": "rm -rf /tmp/data",
                }
            ],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }
        errors = _validate_ai_output(result)
        self.assertTrue(any("不在白名单" in e for e in errors))

    def test_rejects_invalid_metric(self):
        result = {
            "steps": [
                {
                    "risk_level": "低风险",
                    "description": "查看 Pod",
                    "command": "kubectl get pods -n demo",
                }
            ],
            "verification": {"metric": "disk_usage", "operator": "<", "threshold": 80.0},
        }
        errors = _validate_ai_output(result)
        self.assertTrue(any("验证指标" in e for e in errors))

    def test_rejects_invalid_operator(self):
        result = {
            "steps": [
                {
                    "risk_level": "低风险",
                    "description": "查看 Pod",
                    "command": "kubectl get pods -n demo",
                }
            ],
            "verification": {"metric": "cpu", "operator": "==", "threshold": 70.0},
        }
        errors = _validate_ai_output(result)
        self.assertTrue(any("验证操作符" in e for e in errors))

    def test_rejects_invalid_risk_level(self):
        result = {
            "steps": [
                {
                    "risk_level": "极高风险",
                    "description": "删库",
                    "command": "kubectl delete pod test -n demo",
                }
            ],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }
        errors = _validate_ai_output(result)
        self.assertTrue(any("风险等级" in e for e in errors))

    def test_rejects_missing_description(self):
        result = {
            "steps": [
                {
                    "risk_level": "低风险",
                    "command": "kubectl get pods -n demo",
                }
            ],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }
        errors = _validate_ai_output(result)
        self.assertTrue(any("缺少 description" in e for e in errors))

    def test_rejects_missing_command(self):
        result = {
            "steps": [
                {
                    "risk_level": "低风险",
                    "description": "没有命令的步骤",
                }
            ],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }
        errors = _validate_ai_output(result)
        self.assertTrue(any("缺少 command" in e for e in errors))


class BuildFallbackPromptTest(TestCase):
    def test_includes_all_context_sections(self):
        context = {
            "metrics": {
                "cpu": {"current": 92.5},
                "memory": {"current": 536870912},
                "qps": {"current": 150.0},
                "rt_avg": {"current": 1.2},
                "error_rate": {"current": 0.03},
            },
            "logs": [{"line": "Connection refused"}],
            "pods": {"total": 3, "ready": 2, "pods": [{"name": "svc-abc"}]},
            "cmdb": {"owner": "ops-team", "team": "SRE", "dependencies": ["payment-service"]},
        }
        alert = {"alertname": "DiskFull", "service": "order-service", "env": "prod", "severity": "P2", "value": "92%"}
        diagnosis = {"root_cause": "磁盘空间不足"}

        prompt = _build_fallback_prompt(context, alert, diagnosis)

        self.assertIn("DiskFull", prompt)
        self.assertIn("order-service", prompt)
        self.assertIn("CPU使用率: 92.5%", prompt)
        self.assertIn("Connection refused", prompt)
        self.assertIn("svc-abc", prompt)
        self.assertIn("磁盘空间不足", prompt)
        self.assertIn("payment-service", prompt)

    def test_handles_empty_logs_and_pods(self):
        context = {
            "metrics": {},
            "logs": [],
            "pods": {},
            "cmdb": {},
        }
        alert = {"alertname": "UnknownAlert", "service": "test", "env": "staging", "severity": "P3", "value": ""}
        diagnosis = {"root_cause": "未知"}

        prompt = _build_fallback_prompt(context, alert, diagnosis)
        self.assertIn("无错误日志", prompt)


class FallbackSystemPromptTest(TestCase):
    def test_system_prompt_contains_whitelist_commands(self):
        self.assertIn("kubectl scale deployment", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("kubectl delete pod", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("kubectl rollout undo deployment", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("kubectl set resources deployment", FALLBACK_SYSTEM_PROMPT)

    def test_system_prompt_forbids_destructive_actions(self):
        self.assertIn("严禁不可逆操作", FALLBACK_SYSTEM_PROMPT)

    def test_system_prompt_specifies_valid_metrics(self):
        self.assertIn("cpu", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("memory", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("rt_avg", FALLBACK_SYSTEM_PROMPT)
        self.assertIn("error_rate", FALLBACK_SYSTEM_PROMPT)


@patch("agent.agents.fallback.chat_json")
class GenerateAiActionPlanTest(TestCase):
    async def test_returns_structured_plan_on_success(self, mock_chat):
        mock_chat.return_value = {
            "reasoning": "流量上涨导致资源不足",
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "扩容 order-service 到 4 副本",
                    "command": "kubectl scale deployment order-service -n demo --replicas=4",
                }
            ],
            "rollback": "kubectl rollout undo deployment order-service -n demo",
            "verification": {
                "metric": "cpu",
                "operator": "<",
                "threshold": 70.0,
                "description": "CPU 降至 70%",
            },
            "confidence": 0.85,
        }

        from agent.agents.fallback import generate_ai_action_plan

        result = await generate_ai_action_plan(
            context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            alert={"alertname": "UnknownAlert", "service": "test", "env": "prod", "severity": "P2", "value": ""},
            diagnosis={"root_cause": "未知"},
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "ai_fallback")
        self.assertTrue(result["ai_generated"])
        self.assertEqual(len(result["steps"]), 1)
        self.assertEqual(result["steps"][0]["risk_level"], "中风险")
        self.assertEqual(result["confidence"], 0.85)
        self.assertEqual(result["ai_reasoning"], "流量上涨导致资源不足")
        self.assertEqual(result["verification"]["metric"], "cpu")

    async def test_returns_none_when_llm_output_invalid(self, mock_chat):
        mock_chat.return_value = {
            "reasoning": "test",
            "steps": [],  # 空步骤，校验失败
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }

        from agent.agents.fallback import generate_ai_action_plan

        result = await generate_ai_action_plan(
            context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            alert={"alertname": "Unknown", "service": "test", "env": "prod", "severity": "P3", "value": ""},
            diagnosis={"root_cause": "未知"},
        )

        self.assertIsNone(result)

    async def test_returns_none_when_non_whitelist_command(self, mock_chat):
        mock_chat.return_value = {
            "reasoning": "test",
            "steps": [
                {
                    "risk_level": "低风险",
                    "description": "危险操作",
                    "command": "kubectl delete namespace demo",
                }
            ],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
        }

        from agent.agents.fallback import generate_ai_action_plan

        result = await generate_ai_action_plan(
            context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            alert={"alertname": "Unknown", "service": "test", "env": "prod", "severity": "P3", "value": ""},
            diagnosis={"root_cause": "未知"},
        )

        self.assertIsNone(result)

    async def test_returns_none_on_llm_exception(self, mock_chat):
        mock_chat.side_effect = RuntimeError("LLM 超时")

        from agent.agents.fallback import generate_ai_action_plan

        result = await generate_ai_action_plan(
            context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            alert={"alertname": "Unknown", "service": "test", "env": "prod", "severity": "P3", "value": ""},
            diagnosis={"root_cause": "未知"},
        )

        self.assertIsNone(result)
```

- [ ] **Step 3: 运行测试确认失败**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_fallback.py -v
```

期望：全部 FAIL（`generate_ai_action_plan` 等函数尚未定义的真实失败，非 import 错误），因为 `agent/agents/fallback.py` 已创建但可能缺少 import。

若出现 import error，检查 fallback.py 中 `from agent.llm.client import chat_json` 是否可解析。

- [ ] **Step 4: 运行测试确认全部通过**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_fallback.py -v
```

期望：10 个测试全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add agent/agents/fallback.py tests/test_fallback.py
git commit -m "feat: add AI fallback agent for unmatched alert runbooks

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 改造 RCA 流程接入 Fallback Agent

**Files:**
- Modify: `agent/agents/rca.py` — `_build_action_plan()` 改为 async，添加 fallback 调用
- Modify: `agent/agents/rca.py` — `analyze_root_cause()` 调用处改为 `await _build_action_plan()`

- [ ] **Step 1: 改造 `_build_action_plan()` 为 async 并接入 Fallback**

当前 `_build_action_plan()` 代码（`rca.py:168-209`）：

```python
def _build_action_plan(context: dict, alert: dict) -> tuple[dict | None, dict | None]:
    """构建结构化的 Runbook 处置方案及其风险评估"""
    from agent.agents.risk import evaluate_risk
    from agent.agents.runbook import load_runbook, render_runbook

    alert_name = alert.get("alertname", "")
    logger.info(
        "开始生成处置方案: alert=%s, service=%s, env=%s",
        alert_name,
        alert.get("service", "unknown"),
        alert.get("env", "prod"),
    )
    runbook = load_runbook(alert_name)
    if not runbook:
        logger.info("处置方案生成跳过: 未匹配 Runbook, alert=%s", alert_name)
        return None, None

    rendered_steps = render_runbook(
        runbook,
        {
            "service": alert.get("service", ""),
            "env": alert.get("env", "prod"),
            "pods": context.get("pods", {}),
        },
    )
    risk_assessment = evaluate_risk(
        rendered_steps,
        alert.get("severity", "P3"),
        alert.get("service", ""),
        alert.get("env", "prod"),
    )
    logger.info(
        "处置方案生成完成: runbook=%s, steps=%s, risk_level=%s, allowed=%s",
        runbook.name,
        len(rendered_steps),
        risk_assessment.get("level"),
        risk_assessment.get("allowed"),
    )
    return {
        "name": runbook.name,
        "steps": [step.to_dict() for step in rendered_steps],
    }, risk_assessment
```

改为：

```python
async def _build_action_plan(context: dict, alert: dict, diagnosis: dict) -> tuple[dict | None, dict | None]:
    """构建结构化的 Runbook 处置方案及其风险评估

    Runbook 未命中时自动降级为 AI 兜底方案生成。
    """
    from agent.agents.risk import evaluate_risk
    from agent.agents.runbook import load_runbook, render_runbook

    alert_name = alert.get("alertname", "")
    logger.info(
        "开始生成处置方案: alert=%s, service=%s, env=%s",
        alert_name,
        alert.get("service", "unknown"),
        alert.get("env", "prod"),
    )
    runbook = load_runbook(alert_name)
    if not runbook:
        logger.info("未匹配 Runbook，尝试 AI 兜底: alert=%s", alert_name)
        return await _build_ai_fallback_plan(context, alert, diagnosis)

    rendered_steps = render_runbook(
        runbook,
        {
            "service": alert.get("service", ""),
            "env": alert.get("env", "prod"),
            "pods": context.get("pods", {}),
        },
    )
    risk_assessment = evaluate_risk(
        rendered_steps,
        alert.get("severity", "P3"),
        alert.get("service", ""),
        alert.get("env", "prod"),
    )
    logger.info(
        "处置方案生成完成: runbook=%s, steps=%s, risk_level=%s, allowed=%s",
        runbook.name,
        len(rendered_steps),
        risk_assessment.get("level"),
        risk_assessment.get("allowed"),
    )
    return {
        "name": runbook.name,
        "steps": [step.to_dict() for step in rendered_steps],
    }, risk_assessment


async def _build_ai_fallback_plan(context: dict, alert: dict, diagnosis: dict) -> tuple[dict | None, dict | None]:
    """AI 兜底：调用 Fallback Agent 生成处置方案并评估风险"""
    from agent.agents.risk import evaluate_risk
    from agent.agents.runbook import ActionStep
    from agent.agents.fallback import generate_ai_action_plan

    alert_name = alert.get("alertname", "未知")
    service = alert.get("service", "")
    env = alert.get("env", "prod")
    severity = alert.get("severity", "P3")

    ai_plan = await generate_ai_action_plan(context, alert, diagnosis)
    if not ai_plan:
        logger.warning("AI 兜底方案生成失败: alert=%s", alert_name)
        return None, None

    # 将 AI 生成的步骤转为 ActionStep 对象以复用 evaluate_risk()
    ai_steps = [
        ActionStep(
            risk_level=step["risk_level"],
            description=step["description"],
            command=step.get("command", ""),
        )
        for step in ai_plan["steps"]
    ]

    risk_assessment = evaluate_risk(ai_steps, severity, service, env)

    # 注入 AI 标记到风险评估中
    ai_confidence = ai_plan.get("confidence", 0.5)
    risk_assessment["ai_generated"] = True
    risk_assessment["ai_confidence"] = ai_confidence
    risk_assessment["ai_reasoning"] = ai_plan.get("ai_reasoning", "")
    risk_assessment["verification"] = ai_plan.get("verification", {})

    # AI 方案强制追加警示
    risk_assessment.setdefault("warnings", [])
    risk_assessment["warnings"].insert(0, "AI 自主生成方案，未匹配预置 Runbook，请仔细确认")
    risk_assessment.setdefault("factors", [])
    risk_assessment["factors"].append(f"AI 兜底方案，置信度: {ai_confidence:.0%}")

    # AI 自评高风险时强制不允许自动执行，避免 LLM 幻觉导致危险操作
    if risk_assessment.get("level") in ("高风险", "极高风险"):
        risk_assessment["allowed"] = False
        risk_assessment["warnings"].append("AI 自评高风险，已自动禁止自动执行，需人工研判")
        logger.warning(
            "AI 兜底方案被拦截（高风险）: alert=%s, level=%s, score=%s",
            alert_name,
            risk_assessment["level"],
            risk_assessment["score"],
        )

    logger.info(
        "AI 兜底方案生成完成: alert=%s, steps=%s, risk=%s, confidence=%s, allowed=%s",
        alert_name,
        len(ai_plan["steps"]),
        risk_assessment.get("level"),
        ai_confidence,
        risk_assessment.get("allowed"),
    )

    # 返回与 Runbook 路径相同结构的数据，下游无感知
    return {
        "name": ai_plan["name"],
        "steps": ai_plan["steps"],
        "ai_generated": True,
        "ai_reasoning": ai_plan.get("ai_reasoning", ""),
        "verification": ai_plan.get("verification", {}),
        "confidence": ai_confidence,
    }, risk_assessment
```

同时在 `rca.py` 文件顶部新增 import：
```python
# 在现有 import 之后添加（约第6行之后）
from agent.agents.runbook import ActionStep
```

- [ ] **Step 2: 更新 `analyze_root_cause()` 中的调用为 await**

`rca.py:122` 行，将：

```python
runbook, risk_assessment = _build_action_plan(context, alert)
```

改为：

```python
runbook, risk_assessment = await _build_action_plan(context, alert, diagnosis)
```

- [ ] **Step 3: 更新 `_notify_diagnosis()` 展示 AI 标记**

在 `rca.py` `_notify_diagnosis()` 中（约 line 310），`_format_action_plan()` 调用处已能正确处理（`runbook["steps"]` 结构不变）。

但需要在 `_format_action_plan()` 的返回值中追加 AI 标记。找到 `_format_action_plan()` 函数（`rca.py:378-388`），在函数开头加入：

```python
def _format_action_plan(runbook: dict | None) -> str:
    """将结构化 Runbook 步骤格式化为飞书 Markdown"""
    if not runbook:
        return "未匹配到 Runbook，且 AI 兜底方案生成失败，请人工确认处置方案。"
    lines = []
    if runbook.get("ai_generated"):
        lines.append("> **AI 自主分析方案（未匹配预置 Runbook）**\n")
        if runbook.get("ai_reasoning"):
            lines.append(f"> 推理过程：{runbook['ai_reasoning']}\n")
    for index, step in enumerate(runbook.get("steps", []), start=1):
        line = f"{index}. [{step.get('risk_level', '-')}] {step.get('description', '')}"
        if step.get("command"):
            line = f"{line}\n`{step['command']}`"
        lines.append(line)
    return "\n".join(lines) if lines else "Runbook 未提供处置步骤。"
```

同时更新 `_format_risk_warnings()` 以展示 AI 相关 warning（当前已能自动展示，因为 warnings 已注入到 risk_assessment 中，不需要改动）。

- [ ] **Step 4: 运行现有测试确认无回归**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_runbook.py tests/test_risk.py tests/test_phase2_workflow.py -v
```

期望：所有已有测试 PASS（无回归）。

- [ ] **Step 5: 运行新测试确认 Fallback + RCA 集成**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_fallback.py -v
```

期望：10/10 PASS。

- [ ] **Step 6: 提交**

```bash
git add agent/agents/rca.py agent/agents/fallback.py tests/test_fallback.py
git commit -m "feat: wire AI fallback into RCA when runbook not matched

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: 端到端验证（手动 + E2E 脚本）

**Files:**
- Create: 无新文件（使用现有 E2E 脚本验证）

- [ ] **Step 1: 确认项目可启动**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -c "from agent.agents.fallback import generate_ai_action_plan; print('import OK')"
```

期望：`import OK`（无 import 错误）。

- [ ] **Step 2: 运行完整测试套件**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/ -v --ignore=tests/e2e_phase1.sh --ignore=tests/e2e_phase2.sh --ignore=tests/e2e_phase3.sh -k "not e2e" 2>&1 | tail -40
```

期望：所有非 E2E 测试 PASS，无回归。

- [ ] **Step 3: 模拟未知告警验证 AI 兜底流程（需 LLM 可用时执行）**

启动 Agent 后发送一条未匹配 Runbook 的告警：

```bash
# 终端 1：启动 Agent
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && bash ops.sh start

# 终端 2：发送未知告警
curl -X POST http://localhost:8000/api/v1/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": "ops-agent",
    "status": "firing",
    "alerts": [{
      "status": "firing",
      "labels": {
        "alertname": "DiskPressure",
        "service": "order-service",
        "severity": "P2",
        "env": "prod"
      },
      "annotations": {
        "summary": "磁盘使用率超过85%",
        "description": "order-service 节点磁盘使用率 92%"
      },
      "startsAt": "2026-06-06T10:00:00Z",
      "endsAt": "0001-01-01T00:00:00Z",
      "fingerprint": "disk-pressure-test-001"
    }]
  }'
```

检查 Agent 日志输出：
```bash
tail -50 /tmp/ops-agent.log | grep -E "(未匹配 Runbook|Fallback|AI 兜底)"
```

期望日志包含：
- `未匹配 Runbook，尝试 AI 兜底`
- `Fallback Agent 方案生成完成`
- `AI 兜底方案生成完成`

检查数据库中的 incident：
```bash
curl -s http://localhost:8000/api/v1/incidents | python -m json.tool | grep -A5 "DiskPressure"
```

期望：runbook_name 为 `ai_fallback`，risk_assessment 中包含 `ai_generated: true`。

- [ ] **Step 4: 提交（如有修正）**

若 E2E 验证过程中发现并修复了问题：

```bash
git add -A
git commit -m "fix: E2E adjustments for AI fallback flow

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 依赖关系

```
Task 1 (fallback.py) ──→ Task 2 (rca.py 改造) ──→ Task 3 (E2E 验证)
```

Task 2 依赖 Task 1 的 `generate_ai_action_plan` 接口。Task 3 在所有代码完成后执行。

---

## 自检清单

- [x] Spec 覆盖：A1（Fallback Agent）对应 Task 1，A2（RCA 改造）对应 Task 2 Step 1-3，A3（Prompt 调优）对应 Task 1 的 FALLBACK_SYSTEM_PROMPT，A4（风险校验）对应 Task 2 的 `_build_ai_fallback_plan` + 现有 `evaluate_risk` 复用
- [x] 无占位符：所有函数体、测试代码、命令完整写出
- [x] 类型一致性：`generate_ai_action_plan` 返回 `dict | None`，`_build_action_plan` 返回 `tuple[dict|None, dict|None]`，下游通过 `.get()` 安全访问
- [x] 现有接口不变：`_build_action_plan` 签名新增 `diagnosis` 参数且变为 async，但调用方只有一处（`analyze_root_cause`），已同步更新
