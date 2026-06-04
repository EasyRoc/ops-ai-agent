from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch


class Phase3WorkflowTest(IsolatedAsyncioTestCase):
    async def test_execution_workflow_runs_execute_verify_report(self):
        from agent.workflows.alert_workflow import build_execution_workflow

        state = {
            "alert_raw": {},
            "incident_id": "INC-PHASE3",
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service"},
            "context": {"service": "order-service"},
            "diagnosis": {"root_cause": "CPU 过高"},
            "runbook": {"name": "cpu_high.md", "steps": []},
            "risk_assessment": {"allowed": True},
            "approval_status": "approved",
            "execution_result": None,
            "verification_result": None,
            "report": None,
            "operator": "ou_test",
            "error": None,
        }

        async def execute_node(current):
            return {**current, "execution_result": {"status": "success"}}

        async def verify_node(current):
            return {**current, "verification_result": {"recovered": True}}

        async def report_node(current):
            return {**current, "report": {"incident_id": current["incident_id"]}}

        with (
            patch("agent.agents.executor.execute", new=AsyncMock(side_effect=execute_node)),
            patch("agent.agents.verify.verify", new=AsyncMock(side_effect=verify_node)),
            patch("agent.agents.report.report", new=AsyncMock(side_effect=report_node)),
        ):
            result = await build_execution_workflow().ainvoke(state)

        self.assertEqual(result["report"]["incident_id"], "INC-PHASE3")
