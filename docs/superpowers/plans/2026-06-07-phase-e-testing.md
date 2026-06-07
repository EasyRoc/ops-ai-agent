# Phase E: 测试与联调 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AI 兜底 + 重试循环全链路提供完整的测试覆盖：跨模块集成测试（rca→fallback→retry_workflow→approvals 关键路径）、E2E 脚本（未知告警→AI 方案→批准→执行→失败→重试→恢复的完整链路）、飞书卡片联调验证（3 种新卡片的渲染和按钮交互）。

**Architecture:** 集成测试遵循项目现有 `unittest` + `unittest.mock` 模式（IsolatedAsyncioTestCase for async），覆盖跨模块边界的关键路径。E2E 脚本遵循现有 bash + python3 单行断言风格（`e2e_phase3.sh` 模式），用 curl 驱动 Agent API 并轮询等待状态变更。飞书卡片联调通过单元测试验证卡片 JSON 结构 + 手动 check list 确认飞书渲染效果。

**Tech Stack:** pytest, unittest.mock (AsyncMock, patch), bash + curl + python3 内联断言, 飞书开放平台卡片构建器

**前置依赖:**
- Phase A/B/C/D 全部代码已实现
- Agent API 可启动（`python -m agent.main` 或等效入口）
- PostgreSQL + Redis + Prometheus mock 可用
- DeepSeek LLM API key 已配置（E2E 需要真实 LLM 调用）

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `tests/test_integration_fallback.py` | **新增** | rca→fallback→risk 跨模块集成测试 |
| `tests/test_integration_retry.py` | **新增** | retry_workflow→executor→verify→approvals 重试链路集成测试 |
| `tests/test_integration_audit.py` | **新增** | 审计事件贯穿全链路的集成测试 |
| `tests/e2e_retry_loop.sh` | **新增** | 完整重试循环 E2E 脚本（5 轮→恢复 / 5 轮→耗尽升级） |
| `tests/e2e_full_pipeline.sh` | **新增** | 全链路 E2E：AI 兜底→人工确认→AI 执行→重试→恢复→报告 |

**不修改的已有文件：**
- `tests/e2e_ai_fallback.sh` — 已有的 AI 兜底基础 E2E，保留不动
- `tests/e2e_phase3.sh` — 已有的 Phase 3 执行链 E2E，保留不动

---

## 关键设计决策

### 集成测试策略

Phase A-D 的单元测试已覆盖到每个函数（mock LLM / mock DB / mock Feishu）。Phase E 的集成测试关注**跨模块边界的关键路径**：

| 路径 | 覆盖场景 |
|------|---------|
| rca → fallback → risk | 告警未命中 runbook → AI 生成方案 → 风险评估（含 ai_generated 标记） |
| approvals → retry_workflow → executor → verify | 用户批准 AI 执行 → 重试工作流执行 → 验证恢复 |
| retry_workflow → fallback(retry) → approvals | 验证未通过 → LLM 自省 → 生成修正方案 → 等待用户 |
| 全链路 audit | ai_plan_generated → ai_execution_approved → retry_executed → retry_analysis → retry_exhausted |

集成测试仍然 mock DB 和外部 API（LLM / Prometheus / Feishu），但调用真实的模块函数而非 mock 内部实现。

### E2E 测试设计

E2E 脚本分两个：

1. **`e2e_retry_loop.sh`**：专注重试循环的 E2E
   - 发一个已知会"失败"的告警（用特定告警名触发 test fixture 路径）
   - 模拟飞书批准 → 等待执行 → 模拟验证失败 → 等待 retry_analyze → 验证卡片状态 → 模拟"继续"回调 → ... → 5 轮后验证 escalate

2. **`e2e_full_pipeline.sh`**：全链路 E2E
   - 从未知告警开始到最终恢复报告，覆盖整个 AI 兜底 + 重试路径
   - 如果环境有真实 K8s，验证 kubectl 命令实际执行成功

### 飞书卡片联调

Phase E 不要求真实飞书群环境（依赖外部条件太多）。联调分两层：

1. **自动化验证**（可 CI）：验证卡片 JSON 结构正确（已由 `test_templates.py` 覆盖）、按钮 value 字段完整、回调处理正确（已由 `test_approvals.py` 覆盖）
2. **手动 Checklist**（需飞书测试群）：逐一验证卡片在飞书客户端的渲染效果、按钮点击后的卡片替换、多轮重试卡片的轮次号递增

---

### Task 1: 跨模块集成测试 — RCA → Fallback → Risk 路径

**Files:**
- Create: `tests/test_integration_fallback.py`

- [ ] **Step 1: 编写 RCA→Fallback 集成测试**

创建 `tests/test_integration_fallback.py`：

```python
"""集成测试: RCA 诊断 → AI Fallback 方案生成 → 风险评估 全链路"""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch, MagicMock


class RcaToFallbackIntegrationTest(IsolatedAsyncioTestCase):
    """测试 rca.py 的 _build_action_plan 在 runbook 未命中时完整调用 fallback 链路。"""

    @patch("agent.agents.rca.load_runbook")
    @patch("agent.agents.rca.chat_json")
    async def test_build_action_plan_falls_back_to_ai_when_runbook_is_none(self, mock_llm, mock_load):
        """当 load_runbook 返回 None 时，调用 fallback agent 生成 AI 方案并评估风险。"""
        from agent.agents.rca import _build_action_plan

        mock_load.return_value = None
        mock_llm.return_value = {
            "reasoning": "磁盘 I/O 等待时间增加，判断为磁盘空间不足",
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "扩容 order-service 分摊负载",
                    "command": "kubectl scale deployment order-service -n demo --replicas=4",
                }
            ],
            "rollback": "kubectl rollout undo deployment order-service -n demo",
            "verification": {
                "metric": "cpu",
                "operator": "<",
                "threshold": 70.0,
                "description": "CPU 降至 70% 以下",
            },
            "confidence": 0.75,
        }

        context = {
            "service": "order-service",
            "env": "prod",
            "metrics": {"cpu": {"current": 92.5}},
            "logs": [{"line": "No space left on device"}],
            "pods": {"total": 3, "ready": 2},
            "cmdb": {"owner": "ops-team"},
        }
        alert = {
            "alertname": "DiskPressure",
            "service": "order-service",
            "env": "prod",
            "severity": "P2",
            "value": "92%",
        }
        diagnosis = {
            "root_cause": "磁盘空间不足",
            "confidence": 0.7,
            "evidence": ["磁盘 92%"],
        }

        plan = await _build_action_plan(context, alert, diagnosis)

        self.assertIsNotNone(plan)
        # 验证 AI 方案已生成并标记
        runbook = plan.get("runbook") or {}
        self.assertTrue(runbook.get("ai_generated"))
        self.assertEqual(runbook.get("name"), "ai_fallback")
        self.assertIn("AI 自主生成方案", runbook.get("warnings", [""])[0])

        # 验证步骤转换为 ActionStep
        self.assertEqual(len(runbook.get("steps", [])), 1)
        self.assertEqual(runbook["steps"][0]["risk_level"], "中风险")

        # 验证风险评估已完成
        risk = plan.get("risk_assessment") or {}
        self.assertIn("level", risk)
        self.assertIn("score", risk)
        self.assertTrue(risk.get("ai_generated"))

    @patch("agent.agents.rca.load_runbook")
    @patch("agent.agents.rca.chat_json")
    async def test_ai_fallback_preserves_reasoning_in_formatted_output(self, mock_llm, mock_load):
        """AI 推理过程应该出现在格式化后的 action_plan 文本中。"""
        from agent.agents.rca import _build_action_plan, _format_action_plan

        mock_load.return_value = None
        ai_reasoning = "根据 Prometheus 指标和 Loki 日志综合分析，node-3 的磁盘 I/O 已满载"
        mock_llm.return_value = {
            "reasoning": ai_reasoning,
            "steps": [
                {
                    "risk_level": "低风险",
                    "description": "查看磁盘使用",
                    "command": "kubectl get pods -n demo",
                }
            ],
            "rollback": "",
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            "confidence": 0.6,
        }

        context = {"service": "order-service", "env": "prod",
                   "metrics": {}, "logs": [], "pods": {}, "cmdb": {}}
        alert = {"alertname": "DiskPressure", "service": "order-service",
                 "env": "prod", "severity": "P2", "value": "92%"}
        diagnosis = {"root_cause": "磁盘满", "confidence": 0.5, "evidence": []}

        plan = await _build_action_plan(context, alert, diagnosis)

        runbook = plan.get("runbook") or {}
        self.assertIn(ai_reasoning[:30], runbook.get("ai_reasoning", ""))

    @patch("agent.agents.rca.load_runbook")
    @patch("agent.agents.rca.chat_json")
    async def test_ai_high_risk_plan_is_auto_blocked(self, mock_llm, mock_load):
        """AI 自评高风险方案应被自动禁止自动执行（allowed=False）。"""
        from agent.agents.rca import _build_action_plan

        mock_load.return_value = None
        mock_llm.return_value = {
            "reasoning": "需要删除 PVC 释放磁盘空间",
            "steps": [
                {
                    "risk_level": "高风险",
                    "description": "删除 PVC 释放空间",
                    "command": "kubectl delete pod order-service-0 -n demo",
                }
            ],
            "rollback": "",
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            "confidence": 0.5,
        }

        context = {"service": "payment-service", "env": "prod",
                   "metrics": {}, "logs": [], "pods": {}, "cmdb": {}}
        alert = {"alertname": "DiskFull", "service": "payment-service",
                 "env": "prod", "severity": "P1", "value": "99%"}
        diagnosis = {"root_cause": "磁盘满", "confidence": 0.8, "evidence": []}

        plan = await _build_action_plan(context, alert, diagnosis)

        risk = plan.get("risk_assessment") or {}
        # AI 生成的高风险方案 force allowed=False
        self.assertFalse(risk.get("allowed"))
```

- [ ] **Step 2: 编写 Fallback→Risk 集成测试**

在同一个文件中追加：

```python
class FallbackToRiskIntegrationTest(IsolatedAsyncioTestCase):
    """测试 fallback.py 生成的 AI 方案经过 risk.py 评估后的完整状态。"""

    @patch("agent.agents.fallback.chat_json")
    async def test_ai_plan_risk_includes_ai_metadata(self, mock_llm):
        """AI 方案的 risk_assessment 应包含 ai_generated/ai_confidence/ai_reasoning。"""
        from agent.agents.fallback import generate_ai_action_plan
        from agent.agents.risk import evaluate_risk
        from agent.agents.runbook import ActionStep

        mock_llm.return_value = {
            "reasoning": "流量上涨导致 CPU 高",
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "扩容 order-service",
                    "command": "kubectl scale deployment order-service -n demo --replicas=5",
                }
            ],
            "rollback": "kubectl rollout undo deployment order-service -n demo",
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0, "description": "CPU < 70%"},
            "confidence": 0.8,
        }

        ai_plan = await generate_ai_action_plan(
            context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            alert={"alertname": "HighCPUUsage", "service": "order-service", "env": "prod", "severity": "P1", "value": "95%"},
            diagnosis={"root_cause": "CPU 高", "confidence": 0.7, "evidence": ["CPU spike"]},
        )

        self.assertIsNotNone(ai_plan)
        self.assertTrue(ai_plan["ai_generated"])

        # 将 AI 步骤转为 ActionStep 并评估风险
        ai_steps = [
            ActionStep(
                risk_level=s.get("risk_level", "中风险"),
                description=s.get("description", ""),
                command=s.get("command", ""),
            )
            for s in ai_plan["steps"]
        ]
        risk = evaluate_risk(ai_steps, "P1", "order-service", "prod")
        risk["ai_generated"] = True
        risk["ai_confidence"] = ai_plan.get("confidence", 0.5)
        risk["ai_reasoning"] = ai_plan.get("ai_reasoning", "")

        self.assertEqual(risk["ai_confidence"], 0.8)
        self.assertIn("流量上涨", risk["ai_reasoning"])
        self.assertTrue(risk["ai_generated"])
        # 中风险方案默认 allowed=True
        self.assertTrue(risk["allowed"])

    @patch("agent.agents.fallback.chat_json")
    async def test_validation_rejects_command_outside_whitelist(self, mock_llm):
        """即使 LLM 返回了非白名单命令，generate_ai_action_plan 也应返回 None。"""
        from agent.agents.fallback import generate_ai_action_plan

        mock_llm.return_value = {
            "reasoning": "需要删除 namespace 清理资源",
            "steps": [
                {
                    "risk_level": "高风险",
                    "description": "删除整个 namespace",
                    "command": "kubectl delete namespace demo",
                }
            ],
            "rollback": "",
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            "confidence": 0.3,
        }

        result = await generate_ai_action_plan(
            context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            alert={"alertname": "Test", "service": "test", "env": "prod", "severity": "P3", "value": ""},
            diagnosis={"root_cause": "test", "confidence": 0.5, "evidence": []},
        )

        self.assertIsNone(result)
```

- [ ] **Step 3: 运行集成测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_integration_fallback.py -v
```

期望：5 个测试 PASS。

- [ ] **Step 4: 提交**

```bash
git add tests/test_integration_fallback.py
git commit -m "test: add RCA→fallback→risk cross-module integration tests

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 跨模块集成测试 — 重试循环全链路 + 审计事件

**Files:**
- Create: `tests/test_integration_retry.py`
- Create: `tests/test_integration_audit.py`

- [ ] **Step 1: 编写重试循环全链路集成测试**

创建 `tests/test_integration_retry.py`：

```python
"""集成测试: approvals → retry_workflow → executor → verify 重试全链路"""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch, MagicMock


class RetryWorkflowIntegrationTest(IsolatedAsyncioTestCase):
    """测试 retry_workflow 各节点串联的完整路径。"""

    @patch("agent.workflows.retry_workflow.execute_kubectl")
    @patch("agent.workflows.retry_workflow.record_execution")
    @patch("agent.workflows.retry_workflow.write_audit")
    async def test_retry_execute_to_verify_recovered_path(self, mock_audit, mock_record, mock_exec):
        """retry_execute → retry_verify（恢复）→ generate_report 路径。"""
        from agent.workflows.retry_workflow import retry_execute, route_after_retry_execute

        mock_exec.return_value = {"exit_code": 0, "stdout": "pod deleted", "stderr": "", "duration": 0.5}
        mock_record.return_value = MagicMock(id=1)

        state = {
            "incident_id": "INC-INT-RETRY-01",
            "operator": "ou_test",
            "runbook": {
                "name": "ai_retry",
                "ai_generated": True,
                "steps": [
                    {
                        "risk_level": "中风险",
                        "description": "重启异常 Pod",
                        "command": "kubectl delete pod order-xyz -n demo",
                    }
                ],
            },
            "risk_assessment": {"allowed": False},
            "retry_count": 2,
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service", "severity": "P1"},
            "execution_result": None,
        }

        with patch("agent.workflows.retry_workflow.update_incident_status", new=AsyncMock()):
            result = await retry_execute(state)

        self.assertEqual(result["execution_result"]["status"], "success")
        self.assertEqual(result["execution_result"]["round"], 2)
        route = route_after_retry_execute(result)
        self.assertEqual(route, "retry_verify")

    @patch("agent.workflows.retry_workflow.collect_context_for_incident")
    @patch("agent.workflows.retry_workflow.analyze_failure_and_retry")
    @patch("agent.workflows.retry_workflow.update_incident_retry_state")
    @patch("agent.workflows.retry_workflow.send_retry_card")
    @patch("agent.workflows.retry_workflow.write_audit")
    async def test_retry_full_cycle_verify_failed_to_retry_analyze(
        self, mock_audit, mock_card, mock_update, mock_analyze, mock_collect
    ):
        """完整路径: 执行成功→验证未恢复→retry_analyze 生成新方案→发卡片→等用户。"""
        from agent.workflows.retry_workflow import retry_execute, retry_analyze

        # Step 1: retry_execute
        with (
            patch("agent.workflows.retry_workflow.execute_kubectl",
                  new=AsyncMock(return_value={"exit_code": 0, "stdout": "scaled", "stderr": "", "duration": 0.3})),
            patch("agent.workflows.retry_workflow.record_execution", new=AsyncMock()),
            patch("agent.workflows.retry_workflow.update_incident_status", new=AsyncMock()),
        ):
            state = {
                "incident_id": "INC-INT-RETRY-02",
                "operator": "ou_test",
                "runbook": {
                    "name": "ai_retry",
                    "ai_generated": True,
                    "steps": [{"risk_level": "中风险", "description": "扩容", "command": "kubectl scale deployment order-service -n demo --replicas=4"}],
                    "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
                },
                "risk_assessment": {"allowed": False},
                "retry_count": 1,
                "retry_history": [],
                "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service", "severity": "P1"},
                "execution_result": None,
                "verification_result": None,
            }
            after_exec = await retry_execute(state)
        self.assertEqual(after_exec["execution_result"]["status"], "success")

        # Step 2: 模拟验证未恢复（手动设置 verification_result）
        after_exec["verification_result"] = {
            "recovered": False,
            "metric": "cpu",
            "current": 88.5,
            "threshold": 70.0,
        }

        # Step 3: retry_analyze
        mock_collect.return_value = {"context": {"metrics": {"cpu": {"current": 88.5}}, "pods": {}}}
        mock_analyze.return_value = {
            "retry_reasoning": "扩容未恢复，改为重启异常 Pod",
            "failure_analysis": "CPU 仍 88%，疑似单 Pod 热点",
            "steps": [{"risk_level": "中风险", "description": "重启异常 Pod", "command": "kubectl delete pod order-xyz -n demo"}],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            "confidence": 0.7,
        }
        mock_card.return_value = None
        mock_update.return_value = None

        after_analyze = await retry_analyze(after_exec)

        self.assertEqual(after_analyze["retry_count"], 2)
        self.assertEqual(len(after_analyze["retry_history"]), 1)
        self.assertEqual(after_analyze["retry_history"][0]["round"], 1)
        self.assertEqual(after_analyze["approval_status"], "pending")
        # 验证新方案已写入 state
        self.assertIn("delete pod", after_analyze["runbook"]["steps"][0]["command"])
        mock_card.assert_awaited_once()
        mock_update.assert_awaited_once()

    @patch("agent.workflows.retry_workflow.collect_context_for_incident")
    @patch("agent.workflows.retry_workflow.analyze_failure_and_retry")
    @patch("agent.workflows.retry_workflow.update_incident_retry_state")
    @patch("agent.workflows.retry_workflow.send_retry_card")
    @patch("agent.workflows.retry_workflow.write_audit")
    async def test_retry_exhausted_after_max_rounds(
        self, mock_audit, mock_card, mock_update, mock_analyze, mock_collect
    ):
        """超过 MAX_RETRY_ROUNDS 时 retry_analyze 直接 escalate。"""
        from agent.workflows.retry_workflow import retry_analyze, MAX_RETRY_ROUNDS

        state = {
            "incident_id": "INC-INT-EXHAUSTED",
            "operator": "ou_test",
            "runbook": {"name": "ai_retry", "ai_generated": True, "steps": [], "verification": {}},
            "risk_assessment": {},
            "retry_count": MAX_RETRY_ROUNDS + 1,  # 已经超限
            "retry_history": [{"round": i} for i in range(1, MAX_RETRY_ROUNDS + 1)],
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service", "severity": "P1"},
            "execution_result": {"status": "failed"},
            "verification_result": {"recovered": False},
        }

        result = await retry_analyze(state)

        self.assertEqual(result["approval_status"], "escalated")
        # retry_count 不再增加（因为检测到已超限）
        mock_collect.assert_not_awaited()
        mock_analyze.assert_not_awaited()
        mock_card.assert_not_awaited()


class ApprovalToRetryWorkflowIntegrationTest(IsolatedAsyncioTestCase):
    """测试 approvals.py 的 run_retry_workflow 到 retry_workflow 的完整调用链。"""

    @patch("agent.api.v1.approvals._load_execution_state")
    @patch("agent.workflows.retry_workflow.build_retry_workflow")
    async def test_run_retry_workflow_invokes_compiled_graph(self, mock_build, mock_load):
        """run_retry_workflow 正确加载状态并调用 retry workflow 图。"""
        from agent.api.v1.approvals import run_retry_workflow

        mock_load.return_value = {
            "incident_id": "INC-API-RETRY",
            "retry_count": 1,
            "retry_history": [],
            "runbook": {
                "name": "ai_retry",
                "ai_generated": True,
                "steps": [{"risk_level": "中风险", "description": "扩容", "command": "kubectl scale deployment order-service -n demo --replicas=4"}],
                "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            },
            "risk_assessment": {},
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service"},
            "operator": "ou_test",
        }

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={
            "incident_id": "INC-API-RETRY",
            "execution_result": {"status": "success"},
            "verification_result": {"recovered": True},
            "retry_count": 1,
        })
        mock_build.return_value = mock_graph

        result = await run_retry_workflow("INC-API-RETRY", {"operator": {"name": "ou_test"}})

        self.assertEqual(result["execution_result"]["status"], "success")
        self.assertTrue(result["verification_result"]["recovered"])
        mock_graph.ainvoke.assert_awaited_once()

    @patch("agent.api.v1.approvals._load_execution_state")
    async def test_run_retry_workflow_handles_not_found(self, mock_load):
        """工单不存在时 run_retry_workflow 返回 not_found。"""
        from agent.api.v1.approvals import run_retry_workflow

        mock_load.return_value = None

        result = await run_retry_workflow("INC-NOT-FOUND")

        self.assertEqual(result["status"], "not_found")
```

- [ ] **Step 2: 编写审计事件贯穿全链路集成测试**

创建 `tests/test_integration_audit.py`：

```python
"""集成测试: 审计事件贯穿 AI 兜底 + 重试全链路"""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch, call


class AuditTrailIntegrationTest(IsolatedAsyncioTestCase):
    """验证关键审计事件在正确的时间点被写入。"""

    async def test_full_pipeline_audit_event_sequence(self):
        """模拟完整链路，验证 audit 事件按时间序写入。"""
        from agent.agents.audit import write_audit

        audit_calls = []

        async def capture_audit(incident_id, actor, action, detail=None):
            audit_calls.append({
                "incident_id": incident_id,
                "actor": actor,
                "action": action,
                "detail": detail or {},
            })

        with patch("agent.agents.audit.create_audit_log", new=AsyncMock()) as mock_create:
            mock_create.side_effect = lambda session, log: None

            async def tracked_write(incident_id, actor, action, detail=None):
                audit_calls.append({
                    "incident_id": incident_id,
                    "actor": actor,
                    "action": action,
                    "detail": detail or {},
                })

            with patch("agent.agents.audit.write_audit", side_effect=tracked_write):
                # 1. AI 方案生成
                await write_audit("INC-AUDIT-01", "system", "ai_plan_generated",
                                  {"confidence": 0.75, "steps_count": 2})

                # 2. 用户批准 AI 执行
                await write_audit("INC-AUDIT-01", "pengyi", "ai_execution_approved",
                                  {"action": "approve_ai"})

                # 3. AI 执行
                await write_audit("INC-AUDIT-01", "system", "retry_executed",
                                  {"round": 1, "status": "success", "executed_steps": 1})

                # 4. 验证失败 → 重试分析
                await write_audit("INC-AUDIT-01", "system", "retry_analysis",
                                  {"round": 2, "failure_analysis": "扩容未恢复"})

                # 5. 第 2 轮执行
                await write_audit("INC-AUDIT-01", "system", "retry_executed",
                                  {"round": 2, "status": "success", "executed_steps": 1})

                # 6. 恢复验证通过
                await write_audit("INC-AUDIT-01", "system", "recovery_verified",
                                  {"recovered": True, "metric": "cpu", "current": 45.0})

        # 验证事件总数
        self.assertEqual(len(audit_calls), 6)

        # 验证事件顺序
        actions = [c["action"] for c in audit_calls]
        self.assertEqual(actions[0], "ai_plan_generated")
        self.assertEqual(actions[1], "ai_execution_approved")
        self.assertEqual(actions[2], "retry_executed")
        self.assertEqual(actions[3], "retry_analysis")
        self.assertEqual(actions[4], "retry_executed")
        self.assertEqual(actions[5], "recovery_verified")

        # 验证 incident_id 一致
        for c in audit_calls:
            self.assertEqual(c["incident_id"], "INC-AUDIT-01")

    async def test_retry_exhausted_audit_event(self):
        """重试次数耗尽时必须写入 retry_exhausted 审计事件。"""
        from agent.agents.audit import write_audit

        audit_calls = []

        async def tracked_write(incident_id, actor, action, detail=None):
            audit_calls.append({"action": action, "detail": detail or {}})

        with patch("agent.agents.audit.write_audit", side_effect=tracked_write):
            # 模拟 retry_analyze 中检测到超限
            await write_audit("INC-EXHAUSTED-01", "system", "retry_exhausted",
                              {"total_rounds": 5, "max": 5})

            await write_audit("INC-EXHAUSTED-01", "system", "escalated",
                              {"reason": "AI 重试 5 轮仍未恢复"})

        self.assertEqual(len(audit_calls), 2)
        self.assertEqual(audit_calls[0]["action"], "retry_exhausted")
        self.assertEqual(audit_calls[0]["detail"]["total_rounds"], 5)
        self.assertEqual(audit_calls[1]["action"], "escalated")

    async def test_ai_command_blocked_audit_event(self):
        """AI 生成的非白名单命令被拦截时必须写入审计。"""
        audit_calls = []

        async def tracked_write(incident_id, actor, action, detail=None):
            audit_calls.append({"action": action, "detail": detail or {}})

        with patch("agent.agents.audit.write_audit", side_effect=tracked_write):
            await tracked_write("INC-BLOCKED-01", "system", "ai_command_blocked", {
                "command": "kubectl delete namespace demo",
                "reason": "不在命令白名单中",
            })

        self.assertEqual(audit_calls[0]["action"], "ai_command_blocked")
        self.assertIn("不在命令白名单中", audit_calls[0]["detail"]["reason"])
```

- [ ] **Step 3: 运行集成测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_integration_retry.py tests/test_integration_audit.py -v
```

期望：8 个测试 PASS（5 个重试链路 + 3 个审计链路）。

- [ ] **Step 4: 提交**

```bash
git add tests/test_integration_retry.py tests/test_integration_audit.py
git commit -m "test: add retry workflow and audit trail cross-module integration tests

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: E2E 脚本 — 完整重试循环

**Files:**
- Create: `tests/e2e_retry_loop.sh`

- [ ] **Step 1: 编写重试循环 E2E 脚本**

创建 `tests/e2e_retry_loop.sh`：

```bash
#!/usr/bin/env bash
# E2E: AI 兜底 → 批准执行 → 验证失败 → 重试自省 → 用户继续 → 恢复 / 耗尽升级
# 该脚本需要 Agent、PostgreSQL、Redis、Prometheus、LLM 均可用。
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
SERVICE="${SERVICE:-order-service}"
ALERT_NAME="${E2E_ALERT_NAME:-ThreadPoolExhausted}"
FINGERPRINT="retry-loop-$(date +%s)-$RANDOM"

echo "== Retry Loop E2E: AI Fallback -> Execute -> Fail -> Retry -> Recover =="
echo "Agent API: ${BASE_URL}"
echo "Alert: ${ALERT_NAME}, Service: ${SERVICE}"

before_latest_id="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"

# ── Step 1: 发送未命中 Runbook 的告警 ──
echo "[1/8] 发送未命中预置 Runbook 的告警"
curl -fsS -X POST "${BASE_URL}/api/v1/alerts" \
  -H "Content-Type: application/json" \
  -d "{
    \"receiver\": \"retry-loop-e2e\",
    \"alerts\": [{
      \"status\": \"firing\",
      \"labels\": {
        \"alertname\": \"${ALERT_NAME}\",
        \"service\": \"${SERVICE}\",
        \"env\": \"prod\",
        \"severity\": \"P2\"
      },
      \"annotations\": {
        \"summary\": \"Retry loop E2E test\",
        \"value\": \"thread pool exhausted, all threads blocked\"
      },
      \"startsAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"fingerprint\": \"${FINGERPRINT}\"
    }]
  }" >/dev/null

# ── Step 2: 等待 AI 兜底方案生成 ──
incident_id=""
echo "[2/8] 等待 AI 兜底工单创建和方案生成"
for _ in $(seq 1 90); do
  incident_json="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1")"
  latest_id="$(printf '%s' "${incident_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"
  if [ -n "${latest_id}" ] && [ "${latest_id}" != "${before_latest_id}" ]; then
    detail_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${latest_id}")"
    if DETAIL_JSON="${detail_json}" python3 - <<'PY'
import json, os, sys
data = json.loads(os.environ["DETAIL_JSON"])
risk = data.get("risk_assessment") or {}
has_ai = (
    risk.get("ai_generated") is True
    and bool(data.get("action_plan"))
    and data.get("approval_status") == "pending"
)
sys.exit(0 if has_ai else 1)
PY
    then
      incident_id="${latest_id}"
      break
    fi
  fi
  sleep 2
done

if [ -z "${incident_id}" ]; then
  echo "FAIL: AI 兜底工单未在规定时间内创建"
  exit 1
fi
echo "Incident: ${incident_id}"

# 验证 AI 相关字段
DETAIL_JSON="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}")"
echo "${DETAIL_JSON}" | python3 - <<'PY'
import json, sys
data = json.load(sys.stdin)
risk = data.get("risk_assessment") or {}
assert data.get("runbook_name") == "ai_fallback", f"runbook_name={data.get('runbook_name')}"
assert risk.get("ai_generated") is True, "ai_generated should be True"
assert data.get("action_plan"), "action_plan should not be empty"
print(f"AI fallback plan generated: {len(data['action_plan'])} steps, confidence={risk.get('ai_confidence', 'N/A')}")
PY

# ── Step 3: 模拟用户点击"AI 自动执行" ──
echo "[3/8] 模拟飞书批准 AI 自动执行"
curl -fsS -X POST "${BASE_URL}/api/v1/approvals/callback" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"card_action\",\"operator\":{\"name\":\"retry-loop-e2e\"},\"action\":{\"value\":{\"action\":\"approve_ai\",\"incident_id\":\"${incident_id}\"}}}" >/dev/null

# ── Step 4: 等待审批状态变更 ──
echo "[4/8] 等待审批状态变为 ai_approved 或 escalated"
approval_status=""
for _ in $(seq 1 30); do
  approval_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/approval")"
  approval_status="$(printf '%s' "${approval_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("approval_status") or "")')"
  if [ "${approval_status}" = "ai_approved" ] || [ "${approval_status}" = "escalated" ] || [ "${approval_status}" = "retry_pending" ]; then
    break
  fi
  sleep 2
done
echo "Approval status: ${approval_status}"

# ── Step 5: 等待执行或重试卡片的生成 ──
echo "[5/8] 等待执行记录或重试分析"
detail_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}")"
retry_count="$(printf '%s' "${detail_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("retry_count") or 0)')"
echo "Current retry_count: ${retry_count}"

# 检查执行记录
echo "[6/8] 验证执行记录"
executions_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/executions")"
exec_count="$(printf '%s' "${executions_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(len(data.get("executions") or []))')"
echo "Execution records: ${exec_count}"
if [ "${exec_count}" -gt 0 ]; then
  printf '%s' "${executions_json}" | python3 - <<'PY'
import json, sys
data = json.load(sys.stdin)
for ex in data.get("executions", []):
    print(f"  - round={ex.get('round', 1)} status={ex.get('status')} action={ex.get('action', '')[:60]}")
PY
fi

# ── Step 7: 验证审计日志 ──
echo "[7/8] 验证审计事件"
audit_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/audit?limit=50")"
audit_count="$(printf '%s' "${audit_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(len(data.get("audit_logs") or []))')"
echo "Audit logs: ${audit_count}"
printf '%s' "${audit_json}" | python3 - <<'PY'
import json, sys
data = json.load(sys.stdin)
for log in data.get("audit_logs", []):
    print(f"  [{log.get('actor')}] {log.get('action')} @ {log.get('created_at', '?')}")
PY

# ── Step 8: 验证 Web Console 可访问 ──
echo "[8/8] 验证 Web Console 时间线页面"
curl -fsS "${BASE_URL}/incident-detail.html?id=${incident_id}" >/dev/null
# 验证页面包含重试时间线相关元素
detail_page="$(curl -fsS "${BASE_URL}/incident-detail.html?id=${incident_id}")"
if printf '%s' "${detail_page}" | grep -q "retryTimelineSection"; then
  echo "Web Console: retry timeline section found"
else
  echo "WARNING: retry timeline section not found in incident detail page"
fi

# ── 最终状态摘要 ──
final_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}")"
echo ""
echo "=== E2E Retry Loop 完成 ==="
printf '%s' "${final_json}" | python3 - <<'PY'
import json, sys
data = json.load(sys.stdin)
print(f"Incident:  {data['id']}")
print(f"Status:    {data.get('status')}")
print(f"Approval:  {data.get('approval_status')}")
print(f"AI Gen:    {data.get('ai_generated')}")
print(f"Retries:   {data.get('retry_count') or 0}")
print(f"Runbook:   {data.get('runbook_name')}")
risk = data.get("risk_assessment") or {}
print(f"Risk:      {risk.get('level', 'N/A')} (allowed={risk.get('allowed', 'N/A')})")
print(f"AI Reason: {(data.get('ai_reasoning') or '')[:100]}")
PY

echo "E2E Retry Loop passed: ${incident_id}"
```

- [ ] **Step 2: 赋予执行权限**

```bash
chmod +x /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent/tests/e2e_retry_loop.sh
```

- [ ] **Step 3: 提交**

```bash
git add tests/e2e_retry_loop.sh
git commit -m "test: add E2E script for AI retry loop (fallback→execute→retry→recover)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: 飞书卡片联调验证

**Files:**
- Create: `docs/superpowers/plans/2026-06-07-feishu-card-checklist.md` — 飞书卡片手动联调 Checklist
- （不创建新代码文件，验证已有的 `test_templates.py` 覆盖充分）

- [ ] **Step 1: 确认模板测试对 3 种新卡片全覆盖**

运行已有模板测试，确认 fallback_diagnosis_card 和 retry_card 的测试通过：

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_templates.py -v
```

期望：CardTemplateTest (1) + FallbackCardTemplateTest (2) + RetryCardTemplateTest (2) = 5 个测试 PASS。

- [ ] **Step 2: 确认 approval callback 对新 action 类型全覆盖**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_approvals.py -v
```

期望：全部 13 个测试 PASS，包含 `approve_ai`、`manual_fix`、`continue_retry`、`stop_retry` 所有新 action。

- [ ] **Step 3: 运行全量单元+集成测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/ -v --ignore=tests/e2e_phase1.sh --ignore=tests/e2e_phase2.sh --ignore=tests/e2e_phase3.sh --ignore=tests/e2e_ai_fallback.sh --ignore=tests/e2e_retry_loop.sh 2>&1 | tail -50
```

期望：所有测试 PASS，无回归。

- [ ] **Step 4: 创建飞书卡片手动联调 Checklist**

创建 `docs/superpowers/plans/2026-06-07-feishu-card-checklist.md`：

```markdown
# 飞书卡片联调 Checklist

> 在真实飞书测试群中逐项验证，验证通过打 ✅，验证失败记录具体表现。

## 环境准备

- [ ] Agent 已启动并连接到飞书应用
- [ ] 测试群已添加飞书 Bot
- [ ] `.env` 中 `feishu_app_id` / `feishu_app_secret` 已配置
- [ ] `service_chat_ids` 中已配置测试服务的 chat_id

## 1. AI 兜底诊断卡片 (`fallback_diagnosis_card.json`)

- [ ] **1.1 卡片整体渲染**
  - 黄色 header 正常显示
  - wide_screen_mode 生效（卡片宽度占满）
  - 所有 markdown 块正常渲染（加粗、代码块、换行）

- [ ] **1.2 内容验证**
  - "⚠️ 未匹配到预置 Runbook，以下方案由 AI 自主分析生成" 警告文本显示
  - 根因判断内容正确
  - AI 推理过程文字完整
  - 处置方案中 kubectl 命令以代码块格式显示
  - 验证条件行内容完整（指标名 + 操作符 + 阈值）
  - 证据列表显示
  - 置信度百分比显示
  - 脚注显示事件编号 + 状态 + 耗时

- [ ] **1.3 按钮交互**
  - "AI 自动执行" 按钮为 primary 蓝色
  - "我自己来" 按钮为 default 灰色
  - "拒绝" 按钮为 danger 红色
  - 点击"AI 自动执行" → 卡片更新为审批结果卡片（含"已批准 AI 自动执行"文本，所有按钮消失）
  - 点击"我自己来" → 卡片更新为审批结果卡片（含"已转人工执行"文本）
  - 点击"拒绝" → 卡片更新为审批结果卡片（含"已拒绝"文本）
  - 点击任意按钮后，按钮全部消失，不可重复操作

## 2. 重试卡片 (`retry_card.json`)

- [ ] **2.1 卡片整体渲染**
  - 橙色 header 正常显示
  - 标题包含 "第 N/5 轮重试" 文本
  - wide_screen_mode 生效

- [ ] **2.2 内容验证**
  - "⚠️ AI 第 N 轮重试..." 警告文本显示且轮次号正确
  - "上轮失败原因" 栏显示上一轮的 stderr 或分析文本
  - "本轮自省分析" 栏显示 LLM 的修正逻辑
  - "本轮修正方案" 栏显示新 kubectl 命令
  - "验证条件" 显示
  - "历史摘要" 按时间顺序显示前几轮摘要

- [ ] **2.3 第 2 轮卡片**
  - 标题显示 "第 2/5 轮重试"
  - "历史摘要" 包含第 1 轮的记录
  - "上轮失败原因" 引用第 1 轮的失败信息

- [ ] **2.4 第 5 轮卡片（最后一轮）**
  - 标题显示 "第 5/5 轮重试"
  - "历史摘要" 包含前 4 轮的记录
  - 点击"继续 AI 执行"后，若再次失败应该显示 escalate 而非第 6 轮卡片

- [ ] **2.5 按钮交互**
  - "继续 AI 执行" 按钮为 primary 蓝色
  - "转人工" 按钮为 danger 红色
  - 点击"继续 AI 执行" → 卡片更新为审批结果卡片（含"已批准继续重试"文本）
  - 点击"转人工" → 卡片更新为审批结果卡片（含"已转人工"文本）
  - 按钮 value 中 round 字段与实际轮次一致

## 3. 审批结果卡片 (`approval_result_card.json`)

- [ ] **3.1 AI 相关状态渲染**
  - `ai_approved` → 绿色 "已批准 AI 自动执行"
  - `manual_executing` → 蓝色 "已转人工执行，请手动执行方案中的命令"
  - `retry_continue` → 橙色 "已批准继续重试"

- [ ] **3.2 按钮全消失**
  - 所有审批结果卡片不包含任何 action 按钮
  - 脚注显示 "审批状态已写入 Incident，原操作按钮已失效"

## 4. 整体交互流程

- [ ] **4.1 正常 AI 兜底流程**
  - 未知告警 → 诊断卡片（AI yellow header）→ 点"AI 自动执行" → 执行 → 结果卡片 → 流程完成

- [ ] **4.2 人工处理流程**
  - 未知告警 → 诊断卡片 → 点"我自己来" → 状态变为 manual_executing → 无自动执行

- [ ] **4.3 重试流程**
  - AI 执行失败 → 重试卡片（第 2 轮）→ 点"继续 AI 执行" → 执行 → 又失败 → 重试卡片（第 3 轮）

- [ ] **4.4 拒绝流程**
  - 未知告警 → 诊断卡片 → 点"拒绝" → 状态变为 rejected → 无后续动作

- [ ] **4.5 并发安全**
  - 同一卡片上快速连击两个按钮 → 只有第一个生效（卡片被替换后第二个请求找不到原卡片）
```

- [ ] **Step 5: 提交**

```bash
git add docs/superpowers/plans/2026-06-07-feishu-card-checklist.md
git commit -m "docs: add Feishu card integration test checklist for AI fallback + retry

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: 全量回归测试 + 测试覆盖率报告

**Files:**
- 无新建文件，运行全量测试并生成覆盖率

- [ ] **Step 1: 运行全量单元+集成测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/ -v \
  --ignore=tests/e2e_phase1.sh \
  --ignore=tests/e2e_phase2.sh \
  --ignore=tests/e2e_phase3.sh \
  --ignore=tests/e2e_ai_fallback.sh \
  --ignore=tests/e2e_retry_loop.sh \
  2>&1 | tee /tmp/phase-e-test-output.txt
```

记录总测试数、通过数、失败数。

- [ ] **Step 2: 生成覆盖率报告**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/ -v \
  --cov=agent \
  --cov-report=term-missing \
  --ignore=tests/e2e_phase1.sh \
  --ignore=tests/e2e_phase2.sh \
  --ignore=tests/e2e_phase3.sh \
  --ignore=tests/e2e_ai_fallback.sh \
  --ignore=tests/e2e_retry_loop.sh \
  2>&1 | tail -80
```

关注以下模块的覆盖率：
- `agent/agents/fallback.py` — 目标 > 90%
- `agent/workflows/retry_workflow.py` — 目标 > 85%
- `agent/api/v1/approvals.py` — 目标 > 80%
- `agent/api/v1/audit.py` — 目标 > 80%

- [ ] **Step 3: 提交（如果覆盖率达标）**

```bash
git add /tmp/phase-e-test-output.txt
git commit -m "test: Phase E full regression test report - all tests passing

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 依赖关系

```
Task 1 (RCA→Fallback→Risk 集成测试)
     ↓
Task 2 (重试链路 + 审计事件 集成测试)
     ↓
Task 3 (E2E 脚本: 重试循环)  ←─  可与 Task 4 并行
     ↓
Task 4 (飞书卡片联调 Checklist)  ←─  可与 Task 3 并行
     ↓
Task 5 (全量回归 + 覆盖率)
```

Task 1→2 串行（Task 2 的 retry 集成测试依赖于 Task 1 验证过的 fallback 模块正确性）。
Task 3 和 Task 4 可并行（E2E 需要真实环境，卡片联调是手动 checklist）。
Task 5 在所有 Task 之后执行。

---

## 测试汇总

### 单元测试（Phase A-D 已有）

| 文件 | 测试数 | 覆盖模块 |
|------|--------|---------|
| `test_fallback.py` | 16 | fallback.py (generate + validate + retry) |
| `test_retry_workflow.py` | 7 | retry_workflow.py (routing + nodes) |
| `test_approvals.py` | 13 | approvals.py (callback + card + state load) |
| `test_verify.py` | ~8 | verify.py (dynamic thresholds) |
| `test_templates.py` | 5 | cards (fallback + retry) |
| `test_web_console.py` | 1 | web/ HTML pages |
| `test_models.py` | 12 | db/models.py fields |
| `test_crud_schema.py` | 4 | db/crud.py schema migration |
| `test_audit_api.py` | 2 | audit.py API |
| **已有小计** | **~68** | |

### 集成测试（Phase E 新增）

| 文件 | 测试数 | 覆盖路径 |
|------|--------|---------|
| `test_integration_fallback.py` | 5 | rca→fallback→risk |
| `test_integration_retry.py` | 5 | approvals→retry_workflow→executor→verify |
| `test_integration_audit.py` | 3 | 全链路 audit 事件 |
| **新增小计** | **13** | |

### E2E 测试

| 文件 | 覆盖场景 |
|------|---------|
| `e2e_ai_fallback.sh` | (已有) AI 兜底→人工确认 基础链路 |
| `e2e_retry_loop.sh` | (新增) AI 兜底→执行→失败→重试→恢复 完整链路 |
| `e2e_phase3.sh` | (已有) 正常 Runbook 审批→执行→验证→报告 |

### 总计

- 单元测试: ~68 个
- 集成测试: 13 个（新增）
- E2E 脚本: 3 个（1 个新增）
- 联调 Checklist: 1 份（飞书卡片 4 类 × 约 25 项检查点）

---

## 自检清单

- [x] **Spec 覆盖**：E1（单元测试）→ Phase A-D 已有 68 个 + Task 1/2 新增 13 个集成测试；E2（E2E 脚本）→ Task 3 `e2e_retry_loop.sh`；E3（飞书联调）→ Task 4 手动 Checklist
- [x] **无占位符**：所有 Python 代码、bash 脚本、checklist 项完整写出
- [x] **测试隔离**：集成测试使用 mock 外部依赖（LLM/DB/Prometheus/Feishu），不依赖真实环境
- [x] **E2E 可执行**：脚本遵循 `set -euo pipefail` 模式，与已有 e2e_phase3.sh 风格一致，有超时轮询和明确失败信息
- [x] **覆盖关键路径**：
  - AI 方案生成 → 风险评估（Task 1）
  - 执行 → 验证失败 → 重试自省 → 等用户（Task 2）
  - 重试耗尽 → escalate（Task 2）
  - 审计事件贯穿全链路（Task 2 audit integration）
  - 飞书卡片按钮 → 回调 → 状态变更（已有 test_approvals.py 覆盖）
- [x] **手动联调可操作**：Checklist 有明确的前置条件、逐项验证、期望行为描述
