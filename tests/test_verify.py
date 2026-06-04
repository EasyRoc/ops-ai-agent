from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch


class VerifyRecoveryTest(IsolatedAsyncioTestCase):
    async def test_verify_recovery_recovers_when_metric_below_threshold(self):
        from agent.agents.verify import verify_recovery

        with patch("agent.agents.verify.query_service_metrics", new=AsyncMock(return_value={"cpu": {"current": 12.0}})):
            result = await verify_recovery(
                "INC-PHASE3",
                {"service": "order-service"},
                "HighCPUUsage",
                max_wait=1,
                interval=0,
            )

        self.assertTrue(result["recovered"])
        self.assertEqual(result["metric"], "cpu")

    async def test_verify_recovery_escalates_after_timeout(self):
        from agent.agents.verify import verify_recovery

        with patch("agent.agents.verify.query_service_metrics", new=AsyncMock(return_value={"cpu": {"current": 95.0}})):
            result = await verify_recovery(
                "INC-PHASE3",
                {"service": "order-service"},
                "HighCPUUsage",
                max_wait=1,
                interval=0,
            )

        self.assertFalse(result["recovered"])
        self.assertEqual(result["status"], "timeout")

    async def test_verify_node_updates_approval_status_on_failure(self):
        from agent.agents.verify import verify

        state = {
            "incident_id": "INC-PHASE3",
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service"},
            "context": {"service": "order-service"},
            "verification_result": None,
            "approval_status": "approved",
        }

        with (
            patch("agent.agents.verify.verify_recovery", new=AsyncMock(return_value={"recovered": False, "status": "timeout"})),
            patch("agent.agents.verify.update_incident_status", new=AsyncMock()) as update_status,
            patch("agent.agents.verify.write_audit", new=AsyncMock()),
        ):
            result = await verify(state)

        self.assertEqual(result["approval_status"], "escalated")
        update_status.assert_awaited_once()
