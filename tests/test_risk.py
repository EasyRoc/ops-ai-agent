from unittest import TestCase

from agent.agents.risk import evaluate_risk
from agent.agents.runbook import ActionStep


class RiskEvaluationTest(TestCase):
    def test_core_service_with_medium_risk_action_is_medium_risk(self):
        result = evaluate_risk(
            [
                ActionStep(
                    risk_level="中风险",
                    description="扩容 order-service",
                    command="kubectl scale deployment order-service -n demo --replicas=4",
                )
            ],
            alert_severity="P2",
            service="order-service",
            env="prod",
        )

        self.assertEqual(result["level"], "中风险")
        self.assertEqual(result["score"], 40)
        self.assertTrue(result["allowed"])
        self.assertIn("核心服务 order-service", " ".join(result["factors"]))

    def test_rejects_commands_outside_whitelist(self):
        result = evaluate_risk(
            [
                ActionStep(
                    risk_level="高风险",
                    description="执行未知脚本",
                    command="rm -rf /tmp/something",
                )
            ],
            alert_severity="P1",
            service="inventory-service",
            env="prod",
        )

        self.assertEqual(result["level"], "极高风险")
        self.assertFalse(result["allowed"])
        self.assertTrue(any("动作不在白名单" in warning for warning in result["warnings"]))
