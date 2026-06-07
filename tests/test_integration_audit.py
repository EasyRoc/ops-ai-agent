"""集成测试：AI 兜底 + 重试链路中的关键审计事件。"""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch


class AuditTrailIntegrationTest(IsolatedAsyncioTestCase):
    async def test_rca_ai_fallback_writes_ai_plan_generated_event(self):
        from agent.agents.rca import analyze_root_cause

        state = {
            "incident_id": "INC-AUDIT-AI",
            "alert_parsed": {
                "alertname": "UnknownPressure",
                "service": "order-service",
                "env": "prod",
                "severity": "P2",
            },
            "context": {"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
            "diagnosis": None,
            "runbook": None,
            "risk_assessment": None,
            "approval_status": None,
            "error": None,
        }
        ai_plan = {
            "name": "ai_fallback",
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "扩容 order-service",
                    "command": "kubectl scale deployment order-service -n demo --replicas=4",
                }
            ],
            "ai_generated": True,
            "ai_reasoning": "未知告警无预置 Runbook，先做可回滚扩容观察",
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            "confidence": 0.74,
        }

        with (
            patch("agent.agents.rca._diagnose_with_llm", new=AsyncMock(return_value={"root_cause": "未知压力", "confidence": 0.7, "evidence": []})),
            patch("agent.agents.runbook.load_runbook", return_value=None),
            patch("agent.agents.fallback.generate_ai_action_plan", new=AsyncMock(return_value=ai_plan)),
            patch("agent.agents.rca._save_diagnosis", new=AsyncMock()),
            patch("agent.agents.rca._notify_diagnosis", new=AsyncMock()),
            patch("agent.agents.audit.write_audit", new=AsyncMock()) as write_audit,
        ):
            await analyze_root_cause(state)

        actions = [call.args[2] for call in write_audit.await_args_list]
        self.assertIn("ai_plan_generated", actions)

    async def test_retry_execute_writes_command_and_summary_audit_events(self):
        from agent.workflows.retry_workflow import retry_execute

        state = {
            "incident_id": "INC-AUDIT-RETRY",
            "operator": "ou_test",
            "runbook": {
                "name": "ai_retry",
                "ai_generated": True,
                "steps": [{"risk_level": "中风险", "description": "重启 Pod", "command": "kubectl delete pod order-xyz -n demo"}],
            },
            "risk_assessment": {},
            "retry_count": 3,
            "execution_result": None,
        }

        with (
            patch("agent.workflows.retry_workflow.execute_kubectl", new=AsyncMock(return_value={"exit_code": 0, "stdout": "deleted", "stderr": "", "duration": 0.2})),
            patch("agent.workflows.retry_workflow.record_execution", new=AsyncMock()),
            patch("agent.workflows.retry_workflow.update_incident_status", new=AsyncMock()),
            patch("agent.workflows.retry_workflow.write_audit", new=AsyncMock()) as write_audit,
        ):
            await retry_execute(state)

        actions = [call.args[2] for call in write_audit.await_args_list]
        self.assertIn("retry_command_executed", actions)
        self.assertIn("retry_executed", actions)

    async def test_retry_escalate_writes_exhausted_event_when_round_limit_reached(self):
        from agent.workflows.retry_workflow import MAX_RETRY_ROUNDS, retry_escalate

        state = {
            "incident_id": "INC-AUDIT-EXHAUSTED",
            "retry_count": MAX_RETRY_ROUNDS,
            "verification_result": {"reason": "指标未恢复"},
            "execution_result": {},
            "error": None,
        }

        with (
            patch("agent.workflows.retry_workflow.update_incident_status", new=AsyncMock()),
            patch("agent.workflows.retry_workflow.write_audit", new=AsyncMock()) as write_audit,
        ):
            result = await retry_escalate(state)

        self.assertEqual(result["approval_status"], "escalated")
        actions = [call.args[2] for call in write_audit.await_args_list]
        self.assertIn("retry_exhausted", actions)
        self.assertIn("retry_escalated", actions)
