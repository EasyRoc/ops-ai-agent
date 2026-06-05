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

    async def test_duplicate_alert_stops_after_parse(self):
        initial_state = {
            "alert_raw": {"service": "order-service", "fingerprint": "fp-dup"},
            "incident_id": None,
            "alert_parsed": None,
            "context": None,
            "diagnosis": None,
            "duplicate_alert": None,
            "error": None,
        }

        async def parse_duplicate(state):
            return {
                **state,
                "incident_id": "INC-EXISTING",
                "alert_parsed": state["alert_raw"],
                "duplicate_alert": True,
            }

        async def collect_unexpected(state):
            return {**state, "context": {"pods": {}}}

        async def diagnose_unexpected(state):
            return {**state, "diagnosis": {"root_cause": "duplicate should not diagnose"}}

        collect = AsyncMock(side_effect=collect_unexpected)
        diagnose = AsyncMock(side_effect=diagnose_unexpected)

        with (
            patch(
                "agent.agents.alert.parse_and_create_incident",
                new=AsyncMock(side_effect=parse_duplicate),
            ),
            patch(
                "agent.agents.supervisor.collect_context_for_incident",
                new=collect,
            ),
            patch(
                "agent.agents.rca.analyze_root_cause",
                new=diagnose,
            ),
        ):
            result = await build_alert_workflow().ainvoke(initial_state)

        self.assertEqual(result["incident_id"], "INC-EXISTING")
        self.assertTrue(result["duplicate_alert"])
        collect.assert_not_awaited()
        diagnose.assert_not_awaited()
