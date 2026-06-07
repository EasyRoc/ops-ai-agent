from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from agent.agents.runbook import ActionStep


class ExecutorValidationTest(TestCase):
    def test_validate_command_allows_only_whitelisted_kubectl_actions(self):
        from agent.agents.executor import validate_command

        self.assertEqual(validate_command("kubectl scale deployment order-service -n demo --replicas=4"), (True, "medium"))
        self.assertEqual(validate_command("kubectl get pods -n demo"), (True, "low"))
        allowed, reason = validate_command("kubectl delete namespace prod")
        self.assertFalse(allowed)
        self.assertIn("命令不在白名单", reason)

    def test_select_executable_steps_skips_read_only_and_keeps_one_mutating_action(self):
        from agent.agents.executor import select_executable_steps

        steps = [
            ActionStep("低风险", "查看 Pod", "kubectl get pods -n demo"),
            ActionStep("中风险", "扩容", "kubectl scale deployment order-service -n demo --replicas=4"),
            ActionStep("中风险", "重启 Pod", "kubectl delete pod order-service-0 -n demo"),
        ]

        selected = select_executable_steps([step.to_dict() for step in steps])

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["command"], "kubectl scale deployment order-service -n demo --replicas=4")


class ExecutorWorkflowTest(IsolatedAsyncioTestCase):
    async def test_execute_approved_plan_records_success_and_updates_state(self):
        from agent.agents.executor import execute_approved_plan

        state = {
            "incident_id": "INC-PHASE3",
            "operator": "ou_test",
            "runbook": {
                "name": "cpu_high.md",
                "steps": [
                    {"risk_level": "低风险", "description": "查看 Pod", "command": "kubectl get pods -n demo"},
                    {"risk_level": "中风险", "description": "扩容", "command": "kubectl scale deployment order-service -n demo --replicas=4"},
                ],
            },
            "risk_assessment": {"allowed": True, "level": "高风险"},
            "execution_result": None,
            "error": None,
            "retry_count": 3,
        }

        with (
            patch("agent.agents.executor.execute_kubectl", new=AsyncMock(return_value={"exit_code": 0, "stdout": "scaled", "stderr": "", "duration": 0.1})),
            patch("agent.agents.executor.record_execution", new=AsyncMock()) as record_execution,
            patch("agent.agents.executor.write_audit", new=AsyncMock()) as write_audit,
            patch("agent.agents.executor.update_incident_status", new=AsyncMock()),
        ):
            result = await execute_approved_plan(state)

        self.assertEqual(result["execution_result"]["status"], "success")
        self.assertEqual(result["execution_result"]["executed"], 1)
        record_execution.assert_awaited()
        self.assertEqual(record_execution.await_args.kwargs["round_num"], 3)
        write_audit.assert_awaited()

    async def test_execute_approved_plan_escalates_when_risk_not_allowed(self):
        from agent.agents.executor import execute_approved_plan

        state = {
            "incident_id": "INC-BLOCKED",
            "operator": "ou_test",
            "runbook": {"name": "bad.md", "steps": [{"command": "rm -rf /tmp/x"}]},
            "risk_assessment": {"allowed": False},
            "execution_result": None,
            "approval_status": "approved",
            "error": None,
        }

        with (
            patch("agent.agents.executor.write_audit", new=AsyncMock()),
            patch("agent.agents.executor.update_incident_status", new=AsyncMock()),
        ):
            result = await execute_approved_plan(state)

        self.assertEqual(result["approval_status"], "escalated")
        self.assertEqual(result["execution_result"]["status"], "blocked")
