from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch


class RetryWorkflowRoutingTest(TestCase):
    def test_route_after_retry_verify_recovers_to_report(self):
        from agent.workflows.retry_workflow import route_after_retry_verify

        route = route_after_retry_verify({"verification_result": {"recovered": True}})

        self.assertEqual(route, "generate_report")

    def test_route_after_retry_verify_analyzes_when_not_recovered_under_limit(self):
        from agent.workflows.retry_workflow import route_after_retry_verify

        route = route_after_retry_verify(
            {
                "incident_id": "INC-RETRY",
                "retry_count": 2,
                "verification_result": {"recovered": False, "status": "timeout"},
            }
        )

        self.assertEqual(route, "retry_analyze")

    def test_route_after_retry_verify_escalates_after_max_rounds(self):
        from agent.workflows.retry_workflow import MAX_RETRY_ROUNDS, route_after_retry_verify

        route = route_after_retry_verify(
            {
                "incident_id": "INC-RETRY",
                "retry_count": MAX_RETRY_ROUNDS,
                "verification_result": {"recovered": False, "status": "timeout"},
            }
        )

        self.assertEqual(route, "escalate")

    def test_build_retry_workflow_compiles(self):
        from agent.workflows.retry_workflow import build_retry_workflow

        workflow = build_retry_workflow()

        self.assertTrue(hasattr(workflow, "ainvoke"))


class RetryWorkflowNodeTest(IsolatedAsyncioTestCase):
    async def test_update_incident_retry_state_writes_dedicated_columns(self):
        from agent.workflows.retry_workflow import update_incident_retry_state

        runbook = {
            "name": "ai_retry",
            "steps": [{"description": "重启异常 Pod"}],
            "retry_reasoning": "扩容无效，改为重启热点 Pod",
        }
        risk_assessment = {
            "retry": {
                "count": 3,
                "history": [{"round": 1}, {"round": 2}, {"round": 3}],
            }
        }

        with (
            patch("agent.workflows.retry_workflow.AsyncSessionLocal") as session_cls,
            patch("agent.workflows.retry_workflow.update_incident", new=AsyncMock()) as update_incident,
        ):
            session_cls.return_value.__aenter__.return_value = object()
            await update_incident_retry_state("INC-RETRY", runbook, risk_assessment)

        kwargs = update_incident.await_args.kwargs
        self.assertEqual(kwargs["retry_count"], 3)
        self.assertEqual(len(kwargs["retry_history"]), 3)
        self.assertTrue(kwargs["ai_generated"])
        self.assertEqual(kwargs["ai_reasoning"], "扩容无效，改为重启热点 Pod")

    async def test_retry_execute_records_current_retry_round(self):
        from agent.workflows.retry_workflow import retry_execute

        state = {
            "incident_id": "INC-RETRY-EXEC",
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
            "execution_result": None,
        }

        with (
            patch("agent.workflows.retry_workflow.execute_kubectl", new=AsyncMock(return_value={"exit_code": 0, "stdout": "deleted", "stderr": "", "duration": 0.1})),
            patch("agent.workflows.retry_workflow.record_execution", new=AsyncMock()) as record_execution,
            patch("agent.workflows.retry_workflow.write_audit", new=AsyncMock()),
            patch("agent.workflows.retry_workflow.update_incident_status", new=AsyncMock()),
        ):
            result = await retry_execute(state)

        self.assertEqual(result["execution_result"]["status"], "success")
        self.assertEqual(record_execution.await_args.kwargs["round_num"], 2)

    async def test_retry_analyze_persists_new_plan_and_sends_retry_card(self):
        from agent.workflows.retry_workflow import retry_analyze

        state = {
            "incident_id": "INC-RETRY-ANALYZE",
            "operator": "ou_test",
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service", "severity": "P1"},
            "context": {"service": "order-service"},
            "runbook": {
                "name": "ai_fallback",
                "ai_generated": True,
                "ai_reasoning": "初始判断为容量不足",
                "steps": [
                    {
                        "risk_level": "中风险",
                        "description": "扩容",
                        "command": "kubectl scale deployment order-service -n demo --replicas=4",
                    }
                ],
                "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            },
            "risk_assessment": {"allowed": True, "level": "中风险"},
            "execution_result": {"status": "success", "results": []},
            "verification_result": {"recovered": False, "metric": "cpu", "current": 88.0, "threshold": 70.0},
            "retry_count": 1,
            "retry_history": [],
        }
        new_context = {**state, "context": {"service": "order-service", "metrics": {"cpu": {"current": 88.0}}, "pods": {}}}
        retry_plan = {
            "retry_reasoning": "扩容未恢复，改为重启异常 Pod",
            "failure_analysis": "CPU 仍高，疑似单 Pod 热点",
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
            patch("agent.workflows.retry_workflow.collect_context_for_incident", new=AsyncMock(return_value=new_context)),
            patch("agent.workflows.retry_workflow.analyze_failure_and_retry", new=AsyncMock(return_value=retry_plan)),
            patch("agent.workflows.retry_workflow.update_incident_retry_state", new=AsyncMock()) as update_retry,
            patch("agent.workflows.retry_workflow.send_retry_card", new=AsyncMock()) as send_retry_card,
            patch("agent.workflows.retry_workflow.write_audit", new=AsyncMock()),
        ):
            result = await retry_analyze(state)

        self.assertEqual(result["retry_count"], 2)
        self.assertEqual(result["runbook"]["steps"][0]["command"], "kubectl delete pod order-xyz -n demo")
        self.assertEqual(result["risk_assessment"]["retry"]["count"], 2)
        self.assertEqual(result["approval_status"], "pending")
        update_retry.assert_awaited_once()
        send_retry_card.assert_awaited_once()
