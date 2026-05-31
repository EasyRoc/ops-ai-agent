from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agent.workflows.alert_workflow import build_alert_workflow


class AlertWorkflowTest(IsolatedAsyncioTestCase):
    async def test_async_nodes_are_awaited_by_workflow(self):
        initial_state = {
            "alert_raw": {"service": "order-service"},
            "incident_id": None,
            "alert_parsed": None,
            "context": None,
            "diagnosis": None,
            "error": None,
        }

        async def parse(state):
            return {
                **state,
                "incident_id": "INC-TEST",
                "alert_parsed": state["alert_raw"],
            }

        async def collect(state):
            return {**state, "context": {"pods": {}}}

        async def diagnose(state):
            return {**state, "diagnosis": {"root_cause": "test"}}

        with (
            patch(
                "agent.agents.alert.parse_and_create_incident",
                new=AsyncMock(side_effect=parse),
            ),
            patch(
                "agent.agents.supervisor.collect_context_for_incident",
                new=AsyncMock(side_effect=collect),
            ),
            patch(
                "agent.agents.rca.analyze_root_cause",
                new=AsyncMock(side_effect=diagnose),
            ),
        ):
            result = await build_alert_workflow().ainvoke(initial_state)

        self.assertEqual(result["incident_id"], "INC-TEST")
        self.assertEqual(result["diagnosis"], {"root_cause": "test"})
