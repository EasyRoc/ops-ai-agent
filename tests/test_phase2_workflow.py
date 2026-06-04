from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agent.agents.rca import analyze_root_cause
from agent.workflows.alert_workflow import build_alert_workflow


class Phase2WorkflowTest(IsolatedAsyncioTestCase):
    async def test_rca_adds_runbook_risk_and_pending_approval(self):
        state = {
            "alert_raw": {"alertname": "HighCPUUsage"},
            "incident_id": "INC-PHASE2",
            "alert_parsed": {
                "alertname": "HighCPUUsage",
                "service": "order-service",
                "env": "prod",
                "severity": "P1",
            },
            "context": {
                "metrics": {},
                "logs": [],
                "pods": {"total": 2, "ready": 2, "pods": [{"name": "order-service-abc"}]},
                "cmdb": {},
            },
            "diagnosis": None,
            "runbook": None,
            "risk_assessment": None,
            "approval_status": None,
            "error": None,
        }

        with (
            patch(
                "agent.agents.rca._diagnose_with_llm",
                new=AsyncMock(
                    return_value={
                        "root_cause": "CPU 异常升高",
                        "confidence": 0.8,
                        "evidence": ["CPU > 90%"],
                    }
                ),
            ),
            patch("agent.agents.rca._save_diagnosis", new=AsyncMock()),
            patch("agent.agents.rca._notify_diagnosis", new=AsyncMock()),
        ):
            result = await analyze_root_cause(state)

        self.assertEqual(result["approval_status"], "pending")
        self.assertEqual(result["runbook"]["name"], "cpu_high.md")
        self.assertEqual(result["risk_assessment"]["level"], "极高风险")
        self.assertTrue(result["risk_assessment"]["allowed"])
        self.assertTrue(any("kubectl scale deployment order-service" in step["command"] for step in result["runbook"]["steps"]))

    async def test_workflow_preserves_phase2_state_fields(self):
        initial_state = {
            "alert_raw": {"service": "order-service", "alertname": "HighCPUUsage"},
            "incident_id": None,
            "alert_parsed": None,
            "context": None,
            "diagnosis": None,
            "runbook": None,
            "risk_assessment": None,
            "approval_status": None,
            "error": None,
        }

        async def parse(state):
            return {
                **state,
                "incident_id": "INC-PHASE2",
                "alert_parsed": {
                    "alertname": "HighCPUUsage",
                    "service": "order-service",
                    "env": "prod",
                    "severity": "P1",
                },
            }

        async def collect(state):
            return {**state, "context": {"pods": {"total": 2, "pods": []}}}

        async def diagnose(state):
            return {
                **state,
                "diagnosis": {"root_cause": "test"},
                "runbook": {"name": "cpu_high.md", "steps": []},
                "risk_assessment": {"level": "中风险", "allowed": True},
                "approval_status": "pending",
            }

        with (
            patch("agent.agents.alert.parse_and_create_incident", new=AsyncMock(side_effect=parse)),
            patch("agent.agents.supervisor.collect_context_for_incident", new=AsyncMock(side_effect=collect)),
            patch("agent.agents.rca.analyze_root_cause", new=AsyncMock(side_effect=diagnose)),
        ):
            result = await build_alert_workflow().ainvoke(initial_state)

        self.assertEqual(result["approval_status"], "pending")
        self.assertEqual(result["runbook"]["name"], "cpu_high.md")
