from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch


class ResolveVerificationTest(TestCase):
    def test_ai_runbook_verification_overrides_default_threshold(self):
        from agent.agents.verify import _resolve_verification

        state = {
            "runbook": {
                "ai_generated": True,
                "verification": {
                    "metric": "rt_avg",
                    "operator": "<",
                    "threshold": 0.8,
                    "description": "平均响应时间低于 0.8s",
                },
            }
        }

        result = _resolve_verification(state, "HighCPUUsage")

        self.assertEqual(result["metric"], "rt_avg")
        self.assertEqual(result["max"], 0.8)

    def test_non_ai_runbook_uses_default_alert_threshold(self):
        from agent.agents.verify import _resolve_verification

        result = _resolve_verification({"runbook": {"ai_generated": False}}, "HighLatency")

        self.assertEqual(result["metric"], "rt_avg")
        self.assertEqual(result["max"], 1.0)

    def test_ai_runbook_with_unsupported_operator_falls_back_to_default(self):
        from agent.agents.verify import _resolve_verification

        state = {
            "runbook": {
                "ai_generated": True,
                "verification": {
                    "metric": "qps",
                    "operator": ">",
                    "threshold": 10,
                },
            }
        }

        result = _resolve_verification(state, "HighCPUUsage")

        self.assertEqual(result["metric"], "cpu")
        self.assertEqual(result["max"], 70.0)


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

    async def test_verify_recovery_uses_threshold_override(self):
        from agent.agents.verify import verify_recovery

        threshold_override = {"metric": "rt_avg", "max": 0.8, "unit": "s"}
        with patch("agent.agents.verify.query_service_metrics", new=AsyncMock(return_value={"rt_avg": {"current": 0.7}})):
            result = await verify_recovery(
                "INC-PHASE3",
                {"service": "order-service"},
                "HighCPUUsage",
                max_wait=1,
                interval=0,
                threshold_override=threshold_override,
            )

        self.assertTrue(result["recovered"])
        self.assertEqual(result["metric"], "rt_avg")
        self.assertEqual(result["threshold"], 0.8)

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

    async def test_verify_node_passes_ai_threshold_to_recovery_check(self):
        from agent.agents.verify import verify

        state = {
            "incident_id": "INC-AI",
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service"},
            "context": {"service": "order-service"},
            "runbook": {
                "ai_generated": True,
                "verification": {"metric": "rt_avg", "operator": "<", "threshold": 0.75},
            },
            "verification_result": None,
        }

        with (
            patch("agent.agents.verify.verify_recovery", new=AsyncMock(return_value={"recovered": True, "status": "recovered"})) as verify_recovery,
            patch("agent.agents.verify.update_incident_status", new=AsyncMock()),
            patch("agent.agents.verify.write_audit", new=AsyncMock()),
        ):
            result = await verify(state)

        self.assertTrue(result["verification_result"]["recovered"])
        verify_recovery.assert_awaited_once()
        self.assertEqual(verify_recovery.await_args.kwargs["threshold_override"]["metric"], "rt_avg")
        self.assertEqual(verify_recovery.await_args.kwargs["threshold_override"]["max"], 0.75)
