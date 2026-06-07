"""集成测试：approvals -> retry_workflow -> executor -> verify 重试链路。"""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch


class RetryWorkflowIntegrationTest(IsolatedAsyncioTestCase):
    async def test_retry_execute_to_verify_recovered_path(self):
        from agent.workflows.retry_workflow import retry_execute, route_after_retry_execute

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

        with (
            patch("agent.workflows.retry_workflow.execute_kubectl", new=AsyncMock(return_value={"exit_code": 0, "stdout": "pod deleted", "stderr": "", "duration": 0.5})),
            patch("agent.workflows.retry_workflow.record_execution", new=AsyncMock(return_value=MagicMock(id=1))) as record_execution,
            patch("agent.workflows.retry_workflow.write_audit", new=AsyncMock()),
            patch("agent.workflows.retry_workflow.update_incident_status", new=AsyncMock()),
        ):
            result = await retry_execute(state)

        self.assertEqual(result["execution_result"]["status"], "success")
        self.assertEqual(result["execution_result"]["round"], 2)
        self.assertEqual(route_after_retry_execute(result), "retry_verify")
        self.assertEqual(record_execution.await_args.kwargs["round_num"], 2)

    async def test_retry_full_cycle_verify_failed_to_retry_analyze(self):
        from agent.workflows.retry_workflow import retry_analyze, retry_execute

        state = {
            "incident_id": "INC-INT-RETRY-02",
            "operator": "ou_test",
            "runbook": {
                "name": "ai_retry",
                "ai_generated": True,
                "steps": [
                    {
                        "risk_level": "中风险",
                        "description": "扩容",
                        "command": "kubectl scale deployment order-service -n demo --replicas=4",
                    }
                ],
                "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            },
            "risk_assessment": {"allowed": False},
            "retry_count": 1,
            "retry_history": [],
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service", "severity": "P1"},
            "execution_result": None,
            "verification_result": None,
        }

        with (
            patch("agent.workflows.retry_workflow.execute_kubectl", new=AsyncMock(return_value={"exit_code": 0, "stdout": "scaled", "stderr": "", "duration": 0.3})),
            patch("agent.workflows.retry_workflow.record_execution", new=AsyncMock()),
            patch("agent.workflows.retry_workflow.update_incident_status", new=AsyncMock()),
            patch("agent.workflows.retry_workflow.write_audit", new=AsyncMock()),
        ):
            after_exec = await retry_execute(state)

        after_exec["verification_result"] = {
            "recovered": False,
            "metric": "cpu",
            "current": 88.5,
            "threshold": 70.0,
        }

        retry_plan = {
            "retry_reasoning": "扩容未恢复，改为重启异常 Pod",
            "failure_analysis": "CPU 仍 88%，疑似单 Pod 热点",
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "重启异常 Pod",
                    "command": "kubectl delete pod order-xyz -n demo",
                }
            ],
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            "confidence": 0.7,
        }
        with (
            patch("agent.workflows.retry_workflow.collect_context_for_incident", new=AsyncMock(return_value={**after_exec, "context": {"metrics": {"cpu": {"current": 88.5}}, "pods": {}}})),
            patch("agent.workflows.retry_workflow.analyze_failure_and_retry", new=AsyncMock(return_value=retry_plan)),
            patch("agent.workflows.retry_workflow.update_incident_retry_state", new=AsyncMock()) as update_retry,
            patch("agent.workflows.retry_workflow.send_retry_card", new=AsyncMock()) as send_retry_card,
            patch("agent.workflows.retry_workflow.write_audit", new=AsyncMock()),
        ):
            after_analyze = await retry_analyze(after_exec)

        self.assertEqual(after_analyze["retry_count"], 2)
        self.assertEqual(after_analyze["retry_history"][0]["round"], 1)
        self.assertEqual(after_analyze["approval_status"], "pending")
        self.assertIn("delete pod", after_analyze["runbook"]["steps"][0]["command"])
        update_retry.assert_awaited_once()
        send_retry_card.assert_awaited_once()

    async def test_retry_exhausted_after_max_rounds(self):
        from agent.workflows.retry_workflow import MAX_RETRY_ROUNDS, retry_analyze

        state = {
            "incident_id": "INC-INT-EXHAUSTED",
            "operator": "ou_test",
            "runbook": {"name": "ai_retry", "ai_generated": True, "steps": [], "verification": {}},
            "risk_assessment": {},
            "retry_count": MAX_RETRY_ROUNDS,
            "retry_history": [{"round": item} for item in range(1, MAX_RETRY_ROUNDS + 1)],
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service", "severity": "P1"},
            "execution_result": {"status": "failed"},
            "verification_result": {"recovered": False},
        }

        with (
            patch("agent.workflows.retry_workflow.collect_context_for_incident", new=AsyncMock()) as collect_context,
            patch("agent.workflows.retry_workflow.analyze_failure_and_retry", new=AsyncMock()) as analyze_retry,
            patch("agent.workflows.retry_workflow.write_audit", new=AsyncMock()) as write_audit,
        ):
            result = await retry_analyze(state)

        self.assertEqual(result["approval_status"], "escalated")
        self.assertIn("最大轮次", result["error"])
        collect_context.assert_not_awaited()
        analyze_retry.assert_not_awaited()
        write_audit.assert_awaited_once()


class ApprovalToRetryWorkflowIntegrationTest(IsolatedAsyncioTestCase):
    async def test_run_retry_workflow_invokes_compiled_graph(self):
        from agent.api.v1.approvals import run_retry_workflow

        loaded_state = {
            "incident_id": "INC-API-RETRY",
            "retry_count": 1,
            "retry_history": [],
            "runbook": {
                "name": "ai_retry",
                "ai_generated": True,
                "steps": [
                    {
                        "risk_level": "中风险",
                        "description": "扩容",
                        "command": "kubectl scale deployment order-service -n demo --replicas=4",
                    }
                ],
                "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            },
            "risk_assessment": {},
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service"},
            "operator": "ou_test",
        }
        graph = MagicMock()
        graph.ainvoke = AsyncMock(
            return_value={
                "incident_id": "INC-API-RETRY",
                "execution_result": {"status": "success"},
                "verification_result": {"recovered": True},
                "retry_count": 1,
            }
        )

        with (
            patch("agent.api.v1.approvals._load_execution_state", new=AsyncMock(return_value=loaded_state)) as load_state,
            patch("agent.api.v1.approvals.build_retry_workflow", return_value=graph),
        ):
            result = await run_retry_workflow("INC-API-RETRY", {"operator": {"name": "ou_test"}})

        self.assertEqual(result["execution_result"]["status"], "success")
        self.assertTrue(result["verification_result"]["recovered"])
        load_state.assert_awaited_once()
        graph.ainvoke.assert_awaited_once()

    async def test_run_retry_workflow_handles_not_found(self):
        from agent.api.v1.approvals import run_retry_workflow

        with patch("agent.api.v1.approvals._load_execution_state", new=AsyncMock(return_value=None)):
            result = await run_retry_workflow("INC-NOT-FOUND")

        self.assertEqual(result["status"], "not_found")
