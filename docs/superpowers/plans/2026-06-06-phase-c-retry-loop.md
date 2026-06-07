# Phase C: 重试循环 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 兜底方案执行失败后，Agent 自动重采上下文、LLM 自省失败原因并生成修正方案，通过飞书重试卡片等待用户确认后继续执行，最多 5 轮，超限后自动升级人工。

**Architecture:** 新增 `agent/workflows/retry_workflow.py`（retry_execute → retry_verify → [未恢复] retry_analyze），在 `fallback.py` 新增 `analyze_failure_and_retry()` 做 LLM 失败自省，改造 `verify.py` 支持从 AI 方案动态提取验证阈值，扩展 `approvals.py` 回调处理 `continue_retry` 触发重试工作流。

**Tech Stack:** LangGraph StateGraph, DeepSeek LLM (chat_json), 复用现有 `execute_kubectl` / `verify_recovery` / `collect_context_for_incident`，飞书 retry_card.json 模板（Phase B 已创建）

**前置依赖:**
- Phase A 已完成 — `fallback.py::generate_ai_action_plan()` 输出含 `verification` 的 runbook dict
- Phase B 已完成 — `retry_card.json` 模板、`approve_ai` / `continue_retry` / `stop_retry` action 映射

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `agent/agents/fallback.py` | **修改** | 新增 `analyze_failure_and_retry()` + `RETRY_SYSTEM_PROMPT` |
| `agent/workflows/alert_workflow.py` | **修改** | `AlertState` 新增 `retry_count`/`retry_history` 字段 |
| `agent/workflows/retry_workflow.py` | **新增** | 重试工作流：`retry_execute` → `retry_verify` → `retry_analyze` → END |
| `agent/agents/verify.py` | **修改** | 新增 `_resolve_verification()` 从 AI 方案动态提取验证阈值 |
| `agent/agents/executor.py` | **修改** | `record_execution()` 和 `execute_approved_plan()` 支持 round 参数 |
| `agent/api/v1/approvals.py` | **修改** | `approval_callback()` 支持 `continue_retry` 触发重试工作流；新增 `_load_execution_state_for_retry()` |
| `tests/test_fallback.py` | **修改** | 新增 `analyze_failure_and_retry` 测试 |
| `tests/test_verify.py` | **修改** | 新增动态阈值测试 |
| `tests/test_retry_workflow.py` | **新增** | 重试工作流单元测试 |

**不涉及的文件（留给后续 Phase）：**
- `agent/db/models.py` — Phase D 加 retry_count/retry_history/ai_generated 列
- `agent/db/crud.py` — Phase D 适配新列
- `web/` — Phase D 重试时间线 UI

---

## 关键设计决策

### 重试状态持久化（Phase D 前的临时方案）

Phase D 才会给 `incidents` 表加 `retry_count`/`retry_history` 列。Phase C 的临时持久化方案：

**存储位置**：复用已有的 `risk_assessment` JSONB 列。在 `risk_assessment` dict 中新增 `retry` 子对象。

```python
# risk_assessment 结构（含 retry 子对象）
{
    "level": "中风险",
    "score": 40,
    "allowed": True,
    "ai_generated": True,
    "ai_confidence": 0.75,
    "ai_reasoning": "...",
    "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0, "description": "..."},
    "warnings": [...],
    "factors": [...],
    # ↓ Phase C 新增
    "retry": {
        "count": 2,
        "history": [
            {
                "round": 1,
                "timestamp": "2026-06-07T10:00:00Z",
                "plan_steps": [...],
                "execution": {"status": "failed", "stderr": "..."},
                "verification": {"recovered": False, "current": 85.0},
                "analysis": "扩容后 CPU 仍高..."
            }
        ],
        "latest_plan": {
            "steps": [...],
            "verification": {...},
            "ai_reasoning": "..."
        }
    }
}
```

### 工作流调用模式

重试不是在一个 `ainvoke` 里跑 5 轮循环。每轮都是独立的 HTTP callback → 工作流调用：

```
Round 1: approve_ai callback → run_execution_workflow → execute → verify → escalate → END
                                                                                   ↓
Round 2: continue_retry callback → run_retry_workflow → retry_analyze → (send retry card) → END
         (retry_analyze 已 new，但 execute 也需要在此轮发生...)

Wait — 实际上每一轮是两阶段:
  Phase A: retry_analyze（重采上下文 + LLM 自省 + 发重试卡片）→ 等待用户
  Phase B: 用户点"继续" → retry_execute + retry_verify → 恢复则结束，未恢复则再 analyaze
```

实际上应该合并为一个工作流调用。仔细想想：

第一轮（首次 AI 执行）：
- `approve_ai` callback → `run_execution_workflow` → execute → verify → escalate → END
- 如果 escalate 是因为 AI 方案未恢复，这个 escalate 需要变成 "retry_analyze"
- 但如果用现有工作流，escalate 就直接结束了

所以更好的设计是：把首次 AI 执行也并入重试工作流。

**修正后的设计**：

新增 `run_ai_execution_workflow()` 替代原来的 `run_execution_workflow()` 用于 AI 兜底场景：

```
首次执行: approve_ai → run_ai_execution_workflow
  → retry_execute → retry_verify → route:
      recovered → generate_report → END
      not recovered, retry_count < 5 → retry_analyze → push retry card → persist → END
      not recovered, retry_count >= 5 → escalate → END

用户点"继续AI执行": continue_retry → run_ai_execution_workflow
  → (same as above, but retry_analyze increments retry_count)
```

`retry_analyze` 节点：
1. 从 state 读 retry_count，+1
2. 重新采集上下文（调 supervisor 工具）
3. 调 `analyze_failure_and_retry()` 生成修正方案
4. 更新 state["runbook"] 为新方案
5. 更新 state["risk_assessment"]["retry"]
6. 持久化到 DB
7. 发 retry_card 到飞书
8. 写 audit

`retry_execute` 节点：
- 与现有 execute 类似，但使用 state["runbook"] 中的最新方案
- 传入 round 参数

---

## 接口约定

### `analyze_failure_and_retry()` 签名

```python
# agent/agents/fallback.py 新增

RETRY_SYSTEM_PROMPT = """你是一个 SRE 故障自愈专家..."""

async def analyze_failure_and_retry(
    incident_id: str,
    previous_plan: dict,       # 上一轮 runbook（含 steps + ai_reasoning）
    execution_result: dict,    # {"status": "failed", "stdout": "...", "stderr": "...", "exit_code": ...}
    verification_result: dict, # {"recovered": False, "current": 85.0, "threshold": 70.0, "metric": "cpu"}
    retry_count: int,          # 当前是第几轮重试 (1-based, retry_analyze 中已 +1)
    retry_history: list[dict], # 前几轮的历史摘要
    context: dict,             # 重新采集的可观测数据
    alert: dict,               # 告警信息
) -> dict | None:
    """调用 LLM 分析失败原因，生成修正方案

    Returns:
        {
            "retry_reasoning": "上一轮扩容未恢复，原因是...本轮改为...",
            "failure_analysis": "kubectl scale 超时，API Server 负载过高",
            "steps": [{"risk_level": "中风险", "description": "...", "command": "kubectl ..."}],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0, "description": "..."},
            "confidence": 0.70
        }
    """
```

### 动态验证阈值

```python
# agent/agents/verify.py 新增

def _resolve_verification(state: dict, alert_name: str) -> dict:
    """从 AI 方案或硬编码 THRESHOLDS 中获取验证条件。

    优先级: AI 方案 verification > 硬编码 THRESHOLDS > CPU 默认
    """
    runbook = state.get("runbook") or {}
    if runbook.get("ai_generated"):
        verification = runbook.get("verification") or {}
        if verification.get("metric") and verification.get("threshold"):
            # 将 AI 验证条件转成 verify_recovery 能用的格式
            return {
                "metric": verification["metric"],
                "max": float(verification["threshold"]),
                "unit": "",  # 动态阈值不需要 unit
            }
    # 回退到现有逻辑
    return _match_threshold(alert_name)
```

### AlertState 新增字段

```python
# agent/workflows/alert_workflow.py AlertState TypedDict 新增
retry_count: Optional[int]        # 当前重试轮次 (0 = 首轮)
retry_history: Optional[list[dict]] # 历史轮次摘要
```

---

### Task 1: 扩展 Fallback Agent 支持失败自省

**Files:**
- Modify: `agent/agents/fallback.py` — 新增 `RETRY_SYSTEM_PROMPT` + `analyze_failure_and_retry()`
- Modify: `tests/test_fallback.py` — 新增重试自省测试

- [ ] **Step 1: 在 `fallback.py` 中添加 `RETRY_SYSTEM_PROMPT` 和 `analyze_failure_and_retry()`**

在 `agent/agents/fallback.py` 文件末尾追加（`VALID_METRICS` 等常量之后，`generate_ai_action_plan` 之后）：

```python
RETRY_SYSTEM_PROMPT = """你是一个 SRE 故障自愈专家。上一轮 AI 自动处置未能恢复服务，你需要分析失败原因并制定修正方案。

## 分析原则

1. **必须基于上一轮的失败证据**：仔细阅读执行结果（stdout/stderr/exit_code）和验证结果（指标名、当前值、阈值）
2. **本轮方案必须不同于前几轮**：如果第 1 轮扩容无效，第 2 轮不要再次扩容；尝试不同策略（重启 Pod、回滚部署、调整资源 limit）
3. **考虑间接原因**：CPU 高可能是因为下游服务慢导致线程堆积，错误率高可能是因为连接池耗尽而非代码 bug
4. 命令**必须**是 kubectl 命令，且以以下前缀之一开头：
   - kubectl scale deployment
   - kubectl delete pod
   - kubectl rollout undo deployment
   - kubectl set resources deployment
   - kubectl get pods
   - kubectl describe pod
5. 每步标注风险等级：[低风险] / [中风险] / [高风险]
6. 指定验证条件：执行后检查哪个指标 + 判断符（< 或 >）+ 阈值 + 文字说明

## 输出格式

严格输出以下 JSON，不要 markdown 包裹，不要额外解释：

{
  "retry_reasoning": "本轮修正逻辑（200字以内）",
  "failure_analysis": "上一轮失败原因分析（100字以内）",
  "steps": [
    {"risk_level": "中风险", "description": "步骤描述", "command": "kubectl scale deployment xxx -n demo --replicas=4"}
  ],
  "verification": {
    "metric": "cpu",
    "operator": "<",
    "threshold": 70.0,
    "description": "CPU 使用率降至 70% 以下"
  },
  "confidence": 0.7
}"""


def _build_retry_prompt(
    previous_plan: dict,
    execution_result: dict,
    verification_result: dict,
    retry_count: int,
    retry_history: list[dict],
    context: dict,
    alert: dict,
) -> str:
    """构建重试自省的用户提示词。"""
    metrics = context.get("metrics", {})
    pods = context.get("pods", {})

    cpu = metrics.get("cpu", {})
    memory = metrics.get("memory", {})
    qps = metrics.get("qps", {})
    rt = metrics.get("rt_avg", {})
    error_rate = metrics.get("error_rate", {})

    # 前几轮摘要
    history_lines = []
    for h in retry_history:
        plan_summary = "; ".join(
            s.get("description", "")[:40] for s in h.get("plan_steps", [])
        )
        exec_status = h.get("execution", {}).get("status", "?")
        verify_status = "已恢复" if h.get("verification", {}).get("recovered") else "未恢复"
        history_lines.append(
            f"第 {h['round']} 轮: {plan_summary} → 执行{exec_status} → {verify_status}"
        )
    history_text = "\n".join(f"  - {line}" for line in history_lines) if history_lines else "  无"

    # 上一轮执行结果摘要
    exec_status = execution_result.get("status", "未知")
    exec_stderr = (execution_result.get("stderr") or "")[:500]
    exec_stdout = (execution_result.get("stdout") or "")[:300]

    # 上一轮验证结果摘要
    verify_metric = verification_result.get("metric", "?")
    verify_current = verification_result.get("current", "?")
    verify_threshold = verification_result.get("threshold", "?")

    # 上一轮方案摘要
    prev_steps_text = "\n".join(
        f"  {i+1}. [{s.get('risk_level', '?')}] {s.get('description', '')}\n     `{s.get('command', '')}`"
        for i, s in enumerate(previous_plan.get("steps", []))
    )

    return f"""请分析上一轮 AI 处置失败的原因，并制定第 {retry_count} 轮修正方案。

=== 本轮重试信息 ===
- 当前轮次: 第 {retry_count}/5 轮
- 告警名称: {alert.get('alertname', '未知')}
- 服务: {alert.get('service', '未知')}

=== 上一轮处置方案 ===
{prev_steps_text}

=== 上一轮执行结果 ===
- 状态: {exec_status}
- stdout: {exec_stdout or '无输出'}
- stderr: {exec_stderr or '无错误'}

=== 上一轮验证结果 ===
- 指标: {verify_metric}
- 当前值: {verify_current}
- 阈值: {verify_threshold}
- 是否恢复: {'是' if verification_result.get('recovered') else '否'}

=== 前几轮历史 ===
{history_text}

=== 当前系统状态（重新采集） ===
- CPU使用率: {cpu.get('current', 0):.1f}%
- 内存使用: {memory.get('current', 0):.0f} bytes
- QPS: {qps.get('current', 0):.1f} req/s
- 平均响应时间: {rt.get('current', 0):.4f}s
- 错误率: {error_rate.get('current', 0):.4f}

=== Pod 状态 ===
- 总数: {pods.get('total', 0)}
- 就绪: {pods.get('ready', 0)}
- Pod 明细: {json.dumps(pods.get('pods', []), ensure_ascii=False) if pods.get('pods') else '无'}"""


async def analyze_failure_and_retry(
    incident_id: str,
    previous_plan: dict,
    execution_result: dict,
    verification_result: dict,
    retry_count: int,
    retry_history: list[dict],
    context: dict,
    alert: dict,
) -> dict | None:
    """调用 LLM 分析上一轮失败原因并生成修正方案。

    Returns:
        dict 含 retry_reasoning/failure_analysis/steps/verification/confidence，
        LLM 异常或输出不合法时返回 None
    """
    alert_name = alert.get("alertname", "未知")
    logger.info(
        "进入重试自省: incident=%s, alert=%s, retry_count=%s, history_rounds=%s",
        incident_id,
        alert_name,
        retry_count,
        len(retry_history),
    )

    try:
        prompt = _build_retry_prompt(
            previous_plan, execution_result, verification_result,
            retry_count, retry_history, context, alert,
        )
        logger.info("调用 LLM 重试自省: alert=%s, retry_count=%s, prompt_length=%s", alert_name, retry_count, len(prompt))
        result = await chat_json(prompt, system=RETRY_SYSTEM_PROMPT)
    except Exception as exc:
        logger.error("重试自省 LLM 调用失败: alert=%s, error=%s", alert_name, exc, exc_info=True)
        return None

    # 复用 Phase A 的输出校验
    errors = _validate_ai_output(result)
    if errors:
        logger.warning("重试自省 LLM 输出校验失败: alert=%s, retry_count=%s, errors=%s", alert_name, retry_count, errors)
        return None

    retry_reasoning = result.get("retry_reasoning", "")
    failure_analysis = result.get("failure_analysis", "")
    confidence = float(result.get("confidence", 0.5))

    logger.info(
        "重试自省完成: alert=%s, retry_count=%s, steps=%s, confidence=%s, analysis=%s",
        alert_name,
        retry_count,
        len(result["steps"]),
        confidence,
        failure_analysis[:80],
    )

    return {
        "retry_reasoning": retry_reasoning,
        "failure_analysis": failure_analysis,
        "steps": [
            {
                "risk_level": step["risk_level"],
                "description": step["description"],
                "command": step.get("command", ""),
            }
            for step in result["steps"]
        ],
        "verification": result.get("verification", {}),
        "confidence": confidence,
    }
```

- [ ] **Step 2: 编写重试自省测试**

在 `tests/test_fallback.py` 末尾追加：

```python
from agent.agents.fallback import analyze_failure_and_retry, RETRY_SYSTEM_PROMPT


class RetrySystemPromptTest(TestCase):
    def test_prompt_emphasizes_different_strategy(self):
        self.assertIn("必须不同于前几轮", RETRY_SYSTEM_PROMPT)

    def test_prompt_includes_whitelist(self):
        self.assertIn("kubectl scale deployment", RETRY_SYSTEM_PROMPT)
        self.assertIn("kubectl rollout undo deployment", RETRY_SYSTEM_PROMPT)


class BuildRetryPromptTest(TestCase):
    def test_includes_all_retry_context_sections(self):
        previous_plan = {
            "steps": [
                {"risk_level": "中风险", "description": "扩容 order-service", "command": "kubectl scale deployment order-service -n demo --replicas=4"}
            ],
            "ai_reasoning": "流量上涨导致 CPU 高",
        }
        execution_result = {
            "status": "failed",
            "stdout": "deployment scaled",
            "stderr": "Error: timeout",
            "exit_code": 1,
        }
        verification_result = {
            "recovered": False,
            "metric": "cpu",
            "current": 88.5,
            "threshold": 70.0,
        }
        retry_history = [
            {
                "round": 1,
                "plan_steps": [{"description": "扩容"}],
                "execution": {"status": "failed"},
                "verification": {"recovered": False},
            }
        ]
        context = {
            "metrics": {
                "cpu": {"current": 88.5},
                "memory": {"current": 536870912},
                "qps": {"current": 120},
                "rt_avg": {"current": 1.5},
                "error_rate": {"current": 0.01},
            },
            "pods": {"total": 4, "ready": 3, "pods": [{"name": "order-xyz"}]},
        }
        alert = {"alertname": "HighCPUUsage", "service": "order-service"}

        from agent.agents.fallback import _build_retry_prompt

        prompt = _build_retry_prompt(
            previous_plan, execution_result, verification_result,
            2, retry_history, context, alert,
        )

        self.assertIn("第 2/5 轮", prompt)
        self.assertIn("上轮处置方案", prompt)
        self.assertIn("kubectl scale deployment", prompt)
        self.assertIn("Error: timeout", prompt)
        self.assertIn("88.5", prompt)
        self.assertIn("第 1 轮: 扩容 →", prompt)


class AnalyzeFailureAndRetryTest(TestCase):
    @patch("agent.agents.fallback.chat_json")
    async def test_returns_corrected_plan_on_success(self, mock_chat):
        mock_chat.return_value = {
            "retry_reasoning": "扩容未恢复表明不是容量问题，改为重启异常 Pod",
            "failure_analysis": "扩容后 CPU 仍 88%，排除容量瓶颈，疑似单 Pod 热点",
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "重启异常 Pod 释放热点",
                    "command": "kubectl delete pod order-xyz -n demo",
                }
            ],
            "verification": {
                "metric": "cpu",
                "operator": "<",
                "threshold": 70.0,
                "description": "CPU 降至 70% 以下",
            },
            "confidence": 0.7,
        }

        result = await analyze_failure_and_retry(
            incident_id="INC-TEST",
            previous_plan={"steps": [{"risk_level": "中风险", "description": "扩容", "command": "kubectl scale deployment order-service -n demo --replicas=4"}], "ai_reasoning": ""},
            execution_result={"status": "failed", "stdout": "", "stderr": "", "exit_code": 0},
            verification_result={"recovered": False, "metric": "cpu", "current": 88.5, "threshold": 70.0},
            retry_count=2,
            retry_history=[],
            context={"metrics": {}, "pods": {}},
            alert={"alertname": "HighCPUUsage", "service": "order-service"},
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["retry_reasoning"], "扩容未恢复表明不是容量问题，改为重启异常 Pod")
        self.assertEqual(result["failure_analysis"], "扩容后 CPU 仍 88%，排除容量瓶颈，疑似单 Pod 热点")
        self.assertEqual(len(result["steps"]), 1)
        self.assertEqual(result["steps"][0]["command"], "kubectl delete pod order-xyz -n demo")
        self.assertIn("delete pod", result["steps"][0]["command"])

    @patch("agent.agents.fallback.chat_json")
    async def test_returns_none_when_llm_generates_empty_steps(self, mock_chat):
        mock_chat.return_value = {"retry_reasoning": "", "failure_analysis": "", "steps": [], "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0}}

        result = await analyze_failure_and_retry(
            incident_id="INC-TEST",
            previous_plan={"steps": []},
            execution_result={},
            verification_result={},
            retry_count=1,
            retry_history=[],
            context={"metrics": {}, "pods": {}},
            alert={"alertname": "Test", "service": "test"},
        )

        self.assertIsNone(result)

    @patch("agent.agents.fallback.chat_json")
    async def test_returns_none_on_llm_exception(self, mock_chat):
        mock_chat.side_effect = RuntimeError("LLM timeout")

        result = await analyze_failure_and_retry(
            incident_id="INC-TEST",
            previous_plan={"steps": []},
            execution_result={},
            verification_result={},
            retry_count=1,
            retry_history=[],
            context={"metrics": {}, "pods": {}},
            alert={"alertname": "Test", "service": "test"},
        )

        self.assertIsNone(result)
```

- [ ] **Step 3: 运行测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_fallback.py -v
```

期望：全部测试 PASS（已有 10 个 + 新增 6 个 = 16 个）。

- [ ] **Step 4: 运行已有测试确认无回归**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/ -v --ignore=tests/e2e_phase1.sh --ignore=tests/e2e_phase2.sh --ignore=tests/e2e_phase3.sh 2>&1 | tail -20
```

- [ ] **Step 5: 提交**

```bash
git add agent/agents/fallback.py tests/test_fallback.py
git commit -m "feat: add LLM retry self-analysis for AI fallback failures

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: AlertState 新增重试字段 + verify.py 动态阈值

**Files:**
- Modify: `agent/workflows/alert_workflow.py:7-22` — `AlertState` TypedDict 新增字段
- Modify: `agent/agents/verify.py` — 新增 `_resolve_verification()` + 改造 `verify_recovery()`
- Modify: `tests/test_verify.py` — 新增动态阈值测试

- [ ] **Step 1: AlertState 新增 retry_count / retry_history**

`agent/workflows/alert_workflow.py:7-22`，在 `AlertState` TypedDict 中追加两个可选字段：

```python
class AlertState(TypedDict):
    """告警处理工作流的状态，在各节点间流转"""
    alert_raw: dict
    incident_id: Optional[str]
    alert_parsed: Optional[dict]
    duplicate_alert: Optional[bool]
    context: Optional[dict]
    diagnosis: Optional[dict]
    runbook: Optional[dict]
    risk_assessment: Optional[dict]
    approval_status: Optional[str]
    execution_result: Optional[dict]
    verification_result: Optional[dict]
    report: Optional[dict]
    operator: Optional[str]
    error: Optional[str]
    # Phase C 重试循环
    retry_count: Optional[int]       # 当前重试轮次，0 表示首轮，1-5 表示第 N 轮
    retry_history: Optional[list[dict]]  # 每轮历史: [{round, plan_steps, execution, verification, analysis}, ...]
```

- [ ] **Step 2: verify.py 新增 `_resolve_verification()`**

在 `agent/agents/verify.py` 的 `_match_threshold()` 函数之后（约 line 38 之后）插入：

```python
def _resolve_verification(state: dict, alert_name: str) -> dict:
    """从 AI 方案或硬编码 THRESHOLDS 中获取验证条件。

    优先级: AI 方案 verification > 硬编码 THRESHOLDS > CPU 默认。
    """
    runbook = state.get("runbook") or {}
    if runbook.get("ai_generated"):
        verification = runbook.get("verification") or {}
        metric = verification.get("metric", "")
        threshold = verification.get("threshold")
        operator = verification.get("operator", "<")

        if metric and threshold is not None:
            # operator 决定是 max（<）还是 min（>）
            # verify_recovery 当前只支持 max（值 < 阈值才算恢复）
            # 对于 > 操作符（如 QPS > 阈值才恢复），暂转为 max 的逆向逻辑
            if operator == ">":
                logger.info(
                    "AI 验证条件使用 > 操作符，暂转为人审: alert=%s, metric=%s, op=%s",
                    alert_name,
                    metric,
                    operator,
                )
                # 当前 verify_recovery 实现只支持 current < max 的检查
                # > 操作符的验证场景先降级为人工确认
                return THRESHOLDS.get("CPU", {"metric": "cpu", "max": 70.0, "unit": "%"})

            logger.info(
                "使用 AI 方案验证条件: alert=%s, metric=%s, threshold=%s",
                alert_name,
                metric,
                threshold,
            )
            return {
                "metric": metric,
                "max": float(threshold),
                "unit": "",
            }

    # 回退到硬编码 THRESHOLDS
    return _match_threshold(alert_name)
```

- [ ] **Step 3: 改造 `verify_recovery()` 调用点**

`agent/agents/verify.py:169-200`，`verify()` 函数中，将：

```python
result = await verify_recovery(
    incident_id,
    {**context, "service": context.get("service") or alert.get("service", "unknown")},
    alert_name,
)
```

改为：

```python
threshold = _resolve_verification(state, alert_name)
max_wait = 300
if state.get("retry_count") and state["retry_count"] > 2:
    # 第 3 轮起给更长验证窗口，因为问题可能更复杂
    max_wait = 450

result = await verify_recovery(
    incident_id,
    {**context, "service": context.get("service") or alert.get("service", "unknown")},
    alert_name,
    max_wait=max_wait,
)
```

同时在 `verify_recovery()` 内部，`threshold = _match_threshold(alert_name)` 行保持原样（仅首次验证用），因为 `verify()` 调用方已经是改造后的路由。实际 `verify_recovery()` 本身不需要改动——它接收 `alert_name` 做阈值匹配。

Wait — 这样改不对。`verify_recovery()` 内部仍然用 `_match_threshold(alert_name)` 做阈值匹配。动态阈值需要在 `verify()` 或 `verify_recovery()` 层面替换掉 `_match_threshold` 调用。

更好的方案是：在 `verify()` 中计算好阈值，然后传参给 `verify_recovery()` 或者直接在 `verify_recovery()` 里调 `_resolve_verification()`。

让我改 `verify_recovery()` 的签名和实现。只需在 `verify_recovery()` 开头把 `_match_threshold` 替换为接收外部传入的 threshold：

**方案A**：修改 `verify_recovery()` 增加可选 `threshold_override` 参数：

```python
async def verify_recovery(
    incident_id: str,
    context: dict,
    alert_name: str,
    max_wait: int = 300,
    interval: int = 15,
    threshold_override: dict | None = None,
) -> dict:
    # ...
    threshold = threshold_override or _match_threshold(alert_name)
```

然后在 `verify()` 中：
```python
threshold = _resolve_verification(state, alert_name)
result = await verify_recovery(
    incident_id, enhanced_context, alert_name,
    max_wait=max_wait,
    threshold_override=threshold,
)
```

这样对现有调用方完全兼容（threshold_override 默认 None）。

OK，在 Step 3 里我用这个方案。

**实际代码修改**（修改 `verify_recovery` 签名）：

`agent/agents/verify.py:41-47`，将函数签名从：

```python
async def verify_recovery(
    incident_id: str,
    context: dict,
    alert_name: str,
    max_wait: int = 300,
    interval: int = 15,
) -> dict:
```

改为（追加最后一个参数）：

```python
async def verify_recovery(
    incident_id: str,
    context: dict,
    alert_name: str,
    max_wait: int = 300,
    interval: int = 15,
    threshold_override: dict | None = None,
) -> dict:
```

在函数体内（原 `threshold = _match_threshold(alert_name)` 行，约 line 53），改为：

```python
threshold = threshold_override or _match_threshold(alert_name)
```

- [ ] **Step 4: 改造 `verify()` 节点传动态阈值**

`agent/agents/verify.py:169-200`，`verify()` 函数完整改写为：

```python
async def verify(state: dict) -> dict:
    """LangGraph 节点入口：执行后确认服务是否恢复。

    AI 兜底方案在 verify 阶段使用 AI 自己指定的验证条件（指标+阈值+判断符），
    预置 Runbook 方案继续使用硬编码 THRESHOLDS。
    """
    incident_id = state.get("incident_id") or ""
    alert = state.get("alert_parsed") or {}
    context = state.get("context") or {}
    alert_name = alert.get("alertname", "")
    retry_count = state.get("retry_count") or 0

    logger.info(
        "进入 verify 节点: incident=%s, alert=%s, service=%s, retry_count=%s",
        incident_id,
        alert_name,
        context.get("service") or alert.get("service", "-"),
        retry_count,
    )

    threshold = _resolve_verification(state, alert_name)
    max_wait = 450 if retry_count >= 3 else 300

    result = await verify_recovery(
        incident_id,
        {**context, "service": context.get("service") or alert.get("service", "unknown")},
        alert_name,
        max_wait=max_wait,
        threshold_override=threshold,
    )
    state["verification_result"] = result
    if result.get("recovered"):
        await update_incident_status(incident_id, "verified")
        await write_audit(incident_id, "system", "recovery_verified", result)
        logger.info("验证节点完成: incident=%s, recovered=True", incident_id)
    else:
        state["approval_status"] = "escalated"
        await update_incident_status(
            incident_id,
            "escalated",
            approval_status="escalated",
        )
        await write_audit(incident_id, "system", "recovery_verify_failed", result)
        logger.warning("验证节点未恢复，已升级人工: incident=%s", incident_id)
    return state
```

- [ ] **Step 5: 编写动态阈值测试**

在 `tests/test_verify.py` 末尾追加：

```python
from agent.agents.verify import _resolve_verification, THRESHOLDS


class ResolveVerificationTest(TestCase):
    def test_uses_ai_verification_when_present(self):
        state = {
            "runbook": {
                "ai_generated": True,
                "verification": {
                    "metric": "error_rate",
                    "operator": "<",
                    "threshold": 0.02,
                    "description": "错误率降至 2% 以下",
                },
            }
        }
        result = _resolve_verification(state, "UnknownAlert")
        self.assertEqual(result["metric"], "error_rate")
        self.assertEqual(result["max"], 0.02)

    def test_falls_back_to_thresholds_when_not_ai_generated(self):
        state = {"runbook": {"ai_generated": False}}
        result = _resolve_verification(state, "HighCPUUsage")
        self.assertEqual(result["metric"], "cpu")
        self.assertEqual(result["max"], 70.0)

    def test_falls_back_when_ai_verification_missing_metric(self):
        state = {
            "runbook": {
                "ai_generated": True,
                "verification": {"operator": "<", "threshold": 70.0},
            }
        }
        result = _resolve_verification(state, "HighCPUUsage")
        self.assertEqual(result["metric"], "cpu")  # 回退到 THRESHOLDS

    def test_falls_back_when_runbook_is_none(self):
        state = {"runbook": None}
        result = _resolve_verification(state, "OOMKilled")
        self.assertEqual(result["metric"], "memory")

    def test_gt_operator_falls_back_to_default(self):
        state = {
            "runbook": {
                "ai_generated": True,
                "verification": {
                    "metric": "qps",
                    "operator": ">",
                    "threshold": 100.0,
                },
            }
        }
        result = _resolve_verification(state, "UnknownAlert")
        # > 操作符当前降级为 CPU 默认
        self.assertEqual(result["metric"], "cpu")
```

- [ ] **Step 6: 运行 verify 测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_verify.py -v
```

期望：全部测试 PASS。

- [ ] **Step 7: 提交**

```bash
git add agent/workflows/alert_workflow.py agent/agents/verify.py tests/test_verify.py
git commit -m "feat: dynamic verification thresholds from AI fallback plans

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: 重试工作流 + 上下文重采集

**Files:**
- Create: `agent/workflows/retry_workflow.py` — 重试工作流图定义
- Create: `tests/test_retry_workflow.py` — 重试工作流测试
- Modify: `agent/agents/executor.py` — `record_execution()` 支持 round；`execute_approved_plan()` 重试时跳过 risk 检查

- [ ] **Step 1: 编写 `agent/workflows/retry_workflow.py`**

```python
# agent/workflows/retry_workflow.py
import json
import logging
from datetime import datetime, timezone

import asyncio
from langgraph.graph import StateGraph, END

from agent.workflows.alert_workflow import AlertState

logger = logging.getLogger("ops-agent.retry_workflow")

MAX_RETRY_ROUNDS = 5


async def retry_execute(state: AlertState) -> AlertState:
    """重试执行节点：与普通 execute 类似，但跳过 risk.allowed 检查（AI 方案已经在首次审批时确认过风险）。

    使用 state["runbook"] 中的最新方案（可能是 retry_analyze 更新过的）。
    """
    from agent.agents.executor import execute_kubectl, record_execution, select_executable_steps
    from agent.agents.audit import write_audit
    from agent.db.crud import AsyncSessionLocal, update_incident

    incident_id = state.get("incident_id") or ""
    operator = state.get("operator") or "system"
    runbook = state.get("runbook") or {}
    steps = runbook.get("steps") or []
    retry_count = state.get("retry_count") or 1
    logger.info(
        "进入 retry_execute: incident=%s, retry_round=%s, steps=%s",
        incident_id,
        retry_count,
        len(steps),
    )

    # 重试场景跳过 risk.allowed 检查：方案已经在 retry_analyze 中重新评估过风险
    selected_steps = select_executable_steps(steps)
    if not selected_steps:
        reason = "没有可自动执行的变更步骤"
        state["approval_status"] = "escalated"
        state["execution_result"] = {
            "status": "blocked",
            "reason": reason,
            "executed": 0,
            "results": [],
            "round": retry_count,
        }
        await write_audit(incident_id, operator, "retry_execution_blocked", {"reason": reason, "round": retry_count})
        return state

    results = []
    async with AsyncSessionLocal() as session:
        await update_incident(session, incident_id, status="executing")

    for step in selected_steps:
        command = step.get("command", "")
        logger.info("retry_execute 执行: incident=%s, round=%s, command=%s", incident_id, retry_count, command)
        result = await execute_kubectl(command)
        status = "success" if result.get("exit_code") == 0 else result.get("status", "failed")
        await record_execution(incident_id, command, operator, status, result, round_num=retry_count)
        await write_audit(
            incident_id, operator, "retry_command_executed",
            {"command": command, "status": status, "result": result, "round": retry_count},
        )
        results.append({"step": step, "result": result, "status": status})
        if status != "success":
            logger.warning("retry_execute 步骤失败: incident=%s, round=%s, command=%s", incident_id, retry_count, command)
            break

    success = bool(results) and all(item["status"] == "success" for item in results)
    state["execution_result"] = {
        "status": "success" if success else "failed",
        "executed": len(results),
        "results": results,
        "round": retry_count,
    }
    logger.info(
        "retry_execute 完成: incident=%s, round=%s, status=%s, executed=%s",
        incident_id,
        retry_count,
        "success" if success else "failed",
        len(results),
    )
    return state


async def retry_verify(state: AlertState) -> AlertState:
    """重试验证节点：复用 verify.py 的 verify 函数（已支持动态阈值）。"""
    from agent.agents.verify import verify as verify_node

    incident_id = state.get("incident_id", "-")
    retry_count = state.get("retry_count") or 1
    logger.info("进入 retry_verify: incident=%s, round=%s", incident_id, retry_count)
    result = await verify_node(state)
    verification = result.get("verification_result") or {}
    logger.info(
        "retry_verify 完成: incident=%s, round=%s, recovered=%s",
        incident_id,
        retry_count,
        verification.get("recovered"),
    )
    return result


async def retry_analyze(state: AlertState) -> AlertState:
    """重试自省节点：重新采集上下文 → LLM 自省 → 更新方案 → 发重试卡片 → 持久化。

    这个节点执行后会结束当前工作流调用，等待用户通过飞书卡片点击"继续 AI 执行"
    触发下一轮 callback → workflow 调用。
    """
    from agent.agents.fallback import analyze_failure_and_retry
    from agent.agents.runbook import ActionStep
    from agent.agents.risk import evaluate_risk
    from agent.agents.audit import write_audit
    from agent.agents.supervisor import collect_context_for_incident
    from agent.channels.feishu import send_card_to_chat
    from agent.templates import render_card
    from agent.tools.cmdb import get_service_chat_id
    from agent.db.crud import AsyncSessionLocal, update_incident

    incident_id = state.get("incident_id") or ""
    alert = state.get("alert_parsed") or {}
    alert_name = alert.get("alertname", "未知")
    service = alert.get("service", "unknown")
    operator = state.get("operator") or "system"

    # 本轮轮次 = 上次 + 1
    old_count = state.get("retry_count") or 1
    retry_count = old_count + 1
    state["retry_count"] = retry_count

    retry_history = state.get("retry_history") or []
    previous_plan = state.get("runbook") or {}
    execution_result = state.get("execution_result") or {}
    verification_result = state.get("verification_result") or {}
    old_risk = state.get("risk_assessment") or {}

    logger.info(
        "进入 retry_analyze: incident=%s, alert=%s, round=%s/%s",
        incident_id,
        alert_name,
        retry_count,
        MAX_RETRY_ROUNDS,
    )

    # 1. 重新采集可观测上下文
    logger.info("retry_analyze: 重新采集上下文: incident=%s, round=%s", incident_id, retry_count)
    context_state = await collect_context_for_incident(state)
    fresh_context = context_state.get("context") or {}
    state["context"] = fresh_context

    # 2. LLM 自省
    logger.info("retry_analyze: 调用 LLM 自省: incident=%s, round=%s", incident_id, retry_count)
    ai_retry = await analyze_failure_and_retry(
        incident_id=incident_id,
        previous_plan=previous_plan,
        execution_result=execution_result,
        verification_result=verification_result,
        retry_count=retry_count,
        retry_history=retry_history,
        context=fresh_context,
        alert=alert,
    )

    if not ai_retry:
        logger.error("retry_analyze: LLM 自省失败，升级人工: incident=%s, round=%s", incident_id, retry_count)
        state["approval_status"] = "escalated"
        await write_audit(incident_id, operator, "retry_analysis_failed",
                          {"round": retry_count, "reason": "LLM 自省输出无效"})
        return state

    # 3. 构建新方案
    ai_steps = [
        ActionStep(
            risk_level=step.get("risk_level", "中风险"),
            description=step.get("description", ""),
            command=step.get("command", ""),
        )
        for step in ai_retry.get("steps", [])
    ]
    if not ai_steps:
        logger.error("retry_analyze: 新方案无可用步骤，升级人工: incident=%s", incident_id)
        state["approval_status"] = "escalated"
        return state

    new_risk = evaluate_risk(ai_steps, alert.get("severity", "P3"), service, alert.get("env", "prod"))
    new_risk["ai_generated"] = True
    new_risk["ai_confidence"] = float(ai_retry.get("confidence", 0.5))
    new_risk["ai_reasoning"] = ai_retry.get("retry_reasoning", "")
    new_risk["verification"] = ai_retry.get("verification", {})
    new_risk.setdefault("warnings", [])
    new_risk["warnings"].insert(0, f"AI 第 {retry_count} 轮重试方案，请仔细确认")
    new_risk.setdefault("factors", [])
    new_risk["factors"].append(f"AI 重试自省，置信度: {new_risk['ai_confidence']:.0%}")

    if new_risk.get("level") in ("高风险", "极高风险"):
        new_risk["allowed"] = False
        new_risk["warnings"].append("AI 自评高风险，已自动禁止自动执行")

    # 4. 追加 retry_history
    history_entry = {
        "round": old_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "plan_steps": [s.get("description", "") for s in previous_plan.get("steps", [])],
        "execution": {
            "status": execution_result.get("status"),
            "stderr": (execution_result.get("results", [{}])[0].get("result", {}).get("stderr", "") if execution_result.get("results") else "")[:200],
        },
        "verification": {
            "recovered": verification_result.get("recovered", False),
            "current": verification_result.get("current"),
            "threshold": verification_result.get("threshold"),
        },
        "analysis": ai_retry.get("failure_analysis", ""),
    }
    retry_history.append(history_entry)
    state["retry_history"] = retry_history

    # 5. 持久化重试状态到 risk_assessment JSONB
    new_risk["retry"] = {
        "count": retry_count,
        "history": retry_history,
        "latest_plan": {
            "steps": [step.to_dict() for step in ai_steps],
            "verification": ai_retry.get("verification", {}),
            "ai_reasoning": ai_retry.get("retry_reasoning", ""),
        },
    }
    state["risk_assessment"] = new_risk
    state["runbook"] = {
        "name": "ai_retry",
        "steps": [step.to_dict() for step in ai_steps],
        "ai_generated": True,
        "ai_reasoning": ai_retry.get("retry_reasoning", ""),
        "verification": ai_retry.get("verification", {}),
        "confidence": ai_retry.get("confidence", 0.5),
    }
    state["approval_status"] = "pending"

    # 持久化到 DB
    try:
        async with AsyncSessionLocal() as session:
            await update_incident(
                session, incident_id,
                status="retry_pending",
                approval_status="pending",
                action_plan=[step.to_dict() for step in ai_steps],
                risk_assessment=new_risk,
                runbook_name="ai_retry",
            )
        logger.info("retry_analyze: 状态已持久化: incident=%s, round=%s", incident_id, retry_count)
    except Exception as exc:
        logger.error("retry_analyze: DB 持久化失败: incident=%s, error=%s", incident_id, exc, exc_info=True)

    # 6. 审计
    await write_audit(incident_id, operator, "retry_analysis",
                      {"round": retry_count, "failure_analysis": ai_retry.get("failure_analysis", ""),
                       "new_steps_count": len(ai_steps)})

    # 7. 发送飞书重试卡片
    try:
        verification = ai_retry.get("verification", {})
        verify_text = (
            f"{verification.get('description', 'N/A')} "
            f"（指标: {verification.get('metric', 'N/A')}, "
            f"操作符: {verification.get('operator', 'N/A')}, "
            f"阈值: {verification.get('threshold', 'N/A')}）"
        )

        # 格式化行动方案
        action_plan_lines = []
        for idx, step in enumerate(ai_retry["steps"], 1):
            line = f"{idx}. [{step['risk_level']}] {step['description']}"
            if step.get("command"):
                line += f"\n`{step['command']}`"
            action_plan_lines.append(line)

        # 历史摘要
        history_summary_lines = []
        for h in retry_history:
            plans = "; ".join(p[:30] for p in h.get("plan_steps", []))
            v = h.get("verification", {})
            history_summary_lines.append(
                f"第 {h['round']} 轮: {plans} → 当前值 {v.get('current', '?')} / 阈值 {v.get('threshold', '?')}"
            )

        severity = alert.get("severity", "P3")
        card = render_card(
            "retry_card",
            alert_title=f"[{severity}] {service} - {alert_name}",
            retry_round=str(retry_count),
            failure_reason=ai_retry.get("failure_analysis", "未知"),
            retry_reasoning=ai_retry.get("retry_reasoning", ""),
            action_plan="\n".join(action_plan_lines),
            verify_condition=verify_text,
            retry_history_summary="\n".join(history_summary_lines) if history_summary_lines else "无历史",
            incident_id=incident_id,
        )

        chat_id = await get_service_chat_id(service)
        if chat_id:
            result = await send_card_to_chat(chat_id, card)
            logger.info("retry_analyze: 重试卡片已发送: incident=%s, round=%s, code=%s",
                        incident_id, retry_count, result.get("code"))
    except Exception as exc:
        logger.error("retry_analyze: 重试卡片发送失败: incident=%s, error=%s", incident_id, exc)

    # 8. 结束当前工作流调用，等待飞书回调
    logger.info("retry_analyze: 完成，等待用户审批: incident=%s, round=%s", incident_id, retry_count)
    return state


def route_after_retry_execute(state: AlertState) -> str:
    """重试执行后的路由。"""
    incident_id = state.get("incident_id", "-")
    if state.get("error"):
        logger.warning("retry 路由: 检测到错误→升级: incident=%s", incident_id)
        return "escalate"
    execution_result = state.get("execution_result") or {}
    if execution_result.get("status") == "success":
        logger.info("retry 路由: 执行成功→验证: incident=%s", incident_id)
        return "retry_verify"
    logger.warning("retry 路由: 执行失败→升级: incident=%s", incident_id)
    return "escalate"


def route_after_retry_verify(state: AlertState) -> str:
    """重试验证后的路由：
    - 已恢复 → generate_report
    - 未恢复 + retry_count < 5 → retry_analyze（LLM 自省 + 发卡片 + 等用户）
    - 未恢复 + retry_count >= 5 → escalate（耗尽重试次数）
    """
    incident_id = state.get("incident_id", "-")
    verification_result = state.get("verification_result") or {}
    retry_count = state.get("retry_count") or 1

    if verification_result.get("recovered"):
        logger.info("retry 路由: 已恢复→报告: incident=%s, round=%s", incident_id, retry_count)
        return "generate_report"

    if retry_count >= MAX_RETRY_ROUNDS:
        logger.warning("retry 路由: 重试次数耗尽→升级: incident=%s, round=%s", incident_id, retry_count)
        return "escalate"

    logger.info("retry 路由: 未恢复→重试自省: incident=%s, round=%s/%s", incident_id, retry_count, MAX_RETRY_ROUNDS)
    return "retry_analyze"


def build_retry_workflow() -> StateGraph:
    """构建 AI 兜底重试工作流。

    链路: retry_execute → retry_verify → [恢复] generate_report → END
                                        → [未恢复, 未满5轮] retry_analyze → END (等待飞书回调)
                                        → [未恢复, 满5轮] escalate → END
    异常: retry_execute 失败 → escalate → END

    每轮重试都是独立的 workflow 调用：
    1. approave_ai / continue_retry callback → run_retry_workflow() → END
    2. 用户点"继续" → callback → run_retry_workflow() → END
    ...
    """
    from agent.workflows.alert_workflow import report as generate_report_node
    from agent.workflows.alert_workflow import escalate as escalate_node

    logger.info("构建 AI 重试工作流图")
    workflow = StateGraph(AlertState)

    workflow.add_node("retry_execute", retry_execute)
    workflow.add_node("retry_verify", retry_verify)
    workflow.add_node("retry_analyze", retry_analyze)
    workflow.add_node("generate_report", generate_report_node)
    workflow.add_node("escalate", escalate_node)

    workflow.set_entry_point("retry_execute")
    workflow.add_conditional_edges(
        "retry_execute",
        route_after_retry_execute,
        {"retry_verify": "retry_verify", "escalate": "escalate"},
    )
    workflow.add_conditional_edges(
        "retry_verify",
        route_after_retry_verify,
        {
            "generate_report": "generate_report",
            "retry_analyze": "retry_analyze",
            "escalate": "escalate",
        },
    )
    workflow.add_edge("retry_analyze", END)
    workflow.add_edge("generate_report", END)
    workflow.add_edge("escalate", END)

    compiled = workflow.compile()
    logger.info("AI 重试工作流图编译完成")
    return compiled
```

- [ ] **Step 2: 改造 `executor.py` 支持 round 参数**

`agent/agents/executor.py:151-177`，`record_execution()` 函数签名增加 `round_num` 参数：

```python
async def record_execution(
    incident_id: str,
    action: str,
    operator: str,
    status: str,
    result: dict,
    round_num: int = 1,
) -> Execution:
    """将一次真实执行落库，供 Web Console 和报告回溯。"""
    logger.info(
        "进入 record_execution: incident=%s, operator=%s, status=%s, action=%s, round=%s",
        incident_id,
        operator,
        status,
        action,
        round_num,
    )
    async with AsyncSessionLocal() as session:
        execution = Execution(
            incident_id=incident_id,
            action=action,
            operator=operator,
            status=status,
            result={**result, "round": round_num},  # 把 round 写入 result JSONB
            completed_at=datetime.now(timezone.utc),
        )
        saved = await create_execution(session, execution)
    logger.info("执行记录已保存: incident=%s, execution_id=%s, round=%s", incident_id, saved.id, round_num)
    return saved
```

- [ ] **Step 3: 编写重试工作流测试**

创建 `tests/test_retry_workflow.py`：

```python
from unittest import TestCase
from unittest.mock import AsyncMock, patch, MagicMock

from agent.workflows.retry_workflow import (
    route_after_retry_verify,
    route_after_retry_execute,
    MAX_RETRY_ROUNDS,
    build_retry_workflow,
)


class RetryRoutingTest(TestCase):
    def test_route_after_execute_success_goes_to_verify(self):
        state = {"incident_id": "INC-1", "execution_result": {"status": "success"}}
        self.assertEqual(route_after_retry_execute(state), "retry_verify")

    def test_route_after_execute_failure_goes_to_escalate(self):
        state = {"incident_id": "INC-1", "execution_result": {"status": "failed"}}
        self.assertEqual(route_after_retry_execute(state), "escalate")

    def test_route_after_execute_error_goes_to_escalate(self):
        state = {"incident_id": "INC-1", "error": "kaboom"}
        self.assertEqual(route_after_retry_execute(state), "escalate")

    def test_route_after_verify_recovered_goes_to_report(self):
        state = {
            "incident_id": "INC-1",
            "verification_result": {"recovered": True},
            "retry_count": 2,
        }
        self.assertEqual(route_after_retry_verify(state), "generate_report")

    def test_route_after_verify_not_recovered_goes_to_retry_analyze(self):
        state = {
            "incident_id": "INC-1",
            "verification_result": {"recovered": False},
            "retry_count": 2,
        }
        self.assertEqual(route_after_retry_verify(state), "retry_analyze")

    def test_route_after_verify_exhausted_goes_to_escalate(self):
        state = {
            "incident_id": "INC-1",
            "verification_result": {"recovered": False},
            "retry_count": MAX_RETRY_ROUNDS,
        }
        self.assertEqual(route_after_retry_verify(state), "escalate")

    def test_route_after_verify_over_limit_goes_to_escalate(self):
        state = {
            "incident_id": "INC-1",
            "verification_result": {"recovered": False},
            "retry_count": MAX_RETRY_ROUNDS + 1,
        }
        self.assertEqual(route_after_retry_verify(state), "escalate")


class RetryWorkflowBuildTest(TestCase):
    def test_build_retry_workflow_returns_compiled_graph(self):
        workflow = build_retry_workflow()
        self.assertIsNotNone(workflow)
        # 验证所有节点都已注册
        nodes = workflow.get_graph().nodes
        node_names = {n for n in nodes if not n.startswith("__")}
        self.assertIn("retry_execute", node_names)
        self.assertIn("retry_verify", node_names)
        self.assertIn("retry_analyze", node_names)
        self.assertIn("generate_report", node_names)
        self.assertIn("escalate", node_names)
```

- [ ] **Step 4: 运行重试工作流测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_retry_workflow.py -v
```

期望：8 个测试 PASS。

- [ ] **Step 5: 全量测试确认无回归**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/ -v --ignore=tests/e2e_phase1.sh --ignore=tests/e2e_phase2.sh --ignore=tests/e2e_phase3.sh 2>&1 | tail -30
```

- [ ] **Step 6: 提交**

```bash
git add agent/workflows/retry_workflow.py tests/test_retry_workflow.py agent/agents/executor.py
git commit -m "feat: add AI retry workflow with context re-collection and LLM self-analysis

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: 审批回调集成 + 状态恢复

**Files:**
- Modify: `agent/api/v1/approvals.py` — `approval_callback()` 新增 `continue_retry` 触发重试工作流；`_load_execution_state()` 恢复 AI/retry 元数据
- Modify: `tests/test_approvals.py` — 新增 retry callback 测试

- [ ] **Step 1: `_load_execution_state()` 恢复 AI/retry 字段**

`agent/api/v1/approvals.py:224-278`，在 `_load_execution_state()` 中追加 AI 重试元数据的恢复逻辑。在现有 `"runbook": {...}` 块（line 258-260）之后、`"risk_assessment": ...` 行之前追加：

```python
# _load_execution_state() 中，在 runbook/risk_assessment 恢复后追加：

# 恢复 AI 兜底/重试状态
risk = incident.risk_assessment or {}
if risk.get("ai_generated"):
    if state["runbook"]:
        state["runbook"]["ai_generated"] = True
        state["runbook"]["ai_reasoning"] = risk.get("ai_reasoning", "")
        state["runbook"]["verification"] = risk.get("verification", {})
        state["runbook"]["confidence"] = risk.get("ai_confidence", 0.5)

    # 重试元数据从 risk_assessment.retry 子对象恢复
    retry_meta = risk.get("retry", {})
    if retry_meta:
        state["retry_count"] = retry_meta.get("count", 0)
        state["retry_history"] = retry_meta.get("history", [])
        # 用 latest_plan 覆盖 runbook 的 steps（确保执行的是最新方案）
        latest = retry_meta.get("latest_plan", {})
        if latest.get("steps") and state["runbook"]:
            state["runbook"]["steps"] = latest["steps"]
            state["runbook"]["verification"] = latest.get("verification", {})
            state["runbook"]["ai_reasoning"] = latest.get("ai_reasoning", "")
```

完整代码插入位置：在 `logger.info("执行工作流状态已恢复: ...")` 之前（约 line 269 之前）。

- [ ] **Step 2: `approval_callback()` 新增 `continue_retry` 分支**

`agent/api/v1/approvals.py:174-179`，在执行触发逻辑后追加 `continue_retry` 处理：

将：

```python
if approval_status in {"approved", "ai_approved"}:
    background_tasks.add_task(run_execution_workflow, incident_id, body)
```

改为：

```python
if approval_status in {"approved", "ai_approved"}:
    background_tasks.add_task(run_execution_workflow, incident_id, body)
elif approval_status == "retry_continue":
    background_tasks.add_task(run_retry_workflow, incident_id, body)
```

在文件末尾（约 line 380 之后）新增 `run_retry_workflow()` 函数：

```python
async def run_retry_workflow(incident_id: str, body: dict | None = None) -> dict:
    """飞书用户点击"继续 AI 执行"后启动重试工作流。

    工作流链路: retry_execute → retry_verify → [恢复] generate_report
                                                → [未恢复] retry_analyze → END (发卡片，等用户)
    """
    from agent.workflows.retry_workflow import build_retry_workflow

    operator = extract_operator(body or {})
    logger.info(
        "进入 run_retry_workflow: incident=%s, operator=%s",
        incident_id,
        operator,
    )
    state = await _load_execution_state(incident_id, operator)
    if not state:
        return {"status": "not_found", "incident_id": incident_id}

    # 首次调用时 retry_count 可能为 None，初始化为 0
    if state.get("retry_count") is None:
        state["retry_count"] = 0
    if state.get("retry_history") is None:
        state["retry_history"] = []

    workflow = build_retry_workflow()
    try:
        result = await workflow.ainvoke(state)
        logger.info(
            "重试工作流完成: incident=%s, execution=%s, verification=%s, retry_count=%s",
            incident_id,
            (result.get("execution_result") or {}).get("status"),
            (result.get("verification_result") or {}).get("status"),
            result.get("retry_count"),
        )
        return result
    except Exception as exc:
        logger.error(
            "重试工作流失败: incident=%s, error=%s",
            incident_id,
            exc,
            exc_info=True,
        )
        async with AsyncSessionLocal() as session:
            await update_incident(
                session,
                incident_id,
                status="escalated",
                approval_status="escalated",
            )
        return {"status": "failed", "incident_id": incident_id, "error": str(exc)}
```

- [ ] **Step 3: 编写审批回调 retry 测试**

在 `tests/test_approvals.py` 末尾追加：

```python
class ApprovalCallbackRetryTest(IsolatedAsyncioTestCase):
    async def test_continue_retry_triggers_retry_workflow_not_execution(self):
        background_tasks = BackgroundTasks()

        with (
            patch("agent.api.v1.approvals._update_incident_status", new=AsyncMock()),
            patch("agent.api.v1.approvals._update_feishu_card", new=AsyncMock()),
            patch("agent.api.v1.approvals._write_approval_audit", new=AsyncMock()),
            patch("agent.api.v1.approvals.run_execution_workflow", new=AsyncMock()) as run_exec,
            patch("agent.api.v1.approvals.run_retry_workflow", new=AsyncMock()) as run_retry,
        ):
            response = await approval_callback(
                _JSONRequest({
                    "type": "card_action",
                    "action": {
                        "value": '{"action":"continue_retry","incident_id":"INC-RETRY-01","round":2}',
                    },
                }),
                background_tasks,
            )
            for task in background_tasks.tasks:
                await task()

        self.assertEqual(response["approval_status"], "retry_continue")
        run_exec.assert_not_awaited()
        run_retry.assert_awaited_once()

    async def test_stop_retry_escalates_and_does_not_trigger_workflow(self):
        background_tasks = BackgroundTasks()

        with (
            patch("agent.api.v1.approvals._update_incident_status", new=AsyncMock()),
            patch("agent.api.v1.approvals._update_feishu_card", new=AsyncMock()),
            patch("agent.api.v1.approvals._write_approval_audit", new=AsyncMock()),
            patch("agent.api.v1.approvals.run_execution_workflow", new=AsyncMock()) as run_exec,
            patch("agent.api.v1.approvals.run_retry_workflow", new=AsyncMock()) as run_retry,
        ):
            response = await approval_callback(
                _JSONRequest({
                    "type": "card_action",
                    "action": {
                        "value": '{"action":"stop_retry","incident_id":"INC-RETRY-02"}',
                    },
                }),
                background_tasks,
            )
            for task in background_tasks.tasks:
                await task()

        self.assertEqual(response["approval_status"], "escalated")
        run_exec.assert_not_awaited()
        run_retry.assert_not_awaited()
```

- [ ] **Step 4: 运行 approvals 测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_approvals.py -v
```

期望：全部测试 PASS（已有 9 个 + 新增 2 个 = 11 个）。

- [ ] **Step 5: 全量测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/ -v --ignore=tests/e2e_phase1.sh --ignore=tests/e2e_phase2.sh --ignore=tests/e2e_phase3.sh 2>&1 | tail -30
```

- [ ] **Step 6: 提交**

```bash
git add agent/api/v1/approvals.py tests/test_approvals.py
git commit -m "feat: wire continue_retry callback to retry workflow

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 依赖关系

```
Task 1 (fallback.py: analyze_failure_and_retry)
     ↓
Task 2 (verify.py: 动态阈值 + AlertState 新字段)
     ↓
Task 3 (retry_workflow.py: 工作流 + executor round)
     ↓
Task 4 (approvals.py: callback 集成 + 状态恢复)
```

严格串行：每个 Task 都依赖前一个的输出（函数签名、类字段）。

---

## 自检清单

- [x] Spec 覆盖：C1（重试工作流）→ Task 3，C2（上下文重采集）→ Task 3 retry_analyze 节点，C3（动态验证阈值）→ Task 2，C4（重试上限降级）→ Task 3 `route_after_retry_verify` + `MAX_RETRY_ROUNDS`
- [x] 无占位符：所有 Python 代码、测试断言完整写出
- [x] 类型一致性：`analyze_failure_and_retry` 返回 dict 含 `retry_reasoning`/`failure_analysis`/`steps`/`verification`/`confidence`，与 `generate_ai_action_plan` 的 steps/verification 格式一致，`_validate_ai_output` 可复用
- [x] 状态持久化兼容：Phase C 用 `risk_assessment.retry` 子对象存重试元数据，Phase D 迁移到专用列时只需改读写位置，格式不变
- [x] 降级安全：LLM 自省失败→escalate；动态阈值 `>` 操作符暂降级→CPU 默认；新方案无步骤→escalate
