from unittest import TestCase

from agent.agents.runbook import load_runbook, render_runbook


class RunbookTest(TestCase):
    def test_loads_cpu_runbook_and_extracts_risk_steps(self):
        runbook = load_runbook("HighCPUUsage")

        self.assertIsNotNone(runbook)
        self.assertEqual(runbook.name, "cpu_high.md")
        self.assertGreaterEqual(len(runbook.steps), 4)
        self.assertEqual(runbook.steps[0].risk_level, "低风险")
        self.assertIn("Grafana", runbook.steps[0].description)
        self.assertTrue(any(step.command.startswith("kubectl scale deployment") for step in runbook.steps))

    def test_renders_service_replicas_and_pod_placeholders(self):
        runbook = load_runbook("HighCPUUsage")

        steps = render_runbook(
            runbook,
            {
                "service": "order-service",
                "env": "prod",
                "pods": {
                    "total": 2,
                    "pods": [{"name": "order-service-abc"}],
                },
            },
        )

        commands = [step.command for step in steps if step.command]
        self.assertIn("kubectl scale deployment order-service -n demo --replicas=4", commands)
        self.assertIn("kubectl delete pod order-service-abc -n demo", commands)

    def test_demo_scale_target_is_capped_to_avoid_runaway_replicas(self):
        runbook = load_runbook("HighCPUUsage")

        steps = render_runbook(
            runbook,
            {
                "service": "order-service",
                "env": "prod",
                "pods": {
                    "total": 16,
                    "pods": [{"name": "order-service-abc"}],
                },
            },
        )

        commands = [step.command for step in steps if step.command]
        self.assertIn("kubectl scale deployment order-service -n demo --replicas=4", commands)

    def test_returns_none_for_unknown_alert(self):
        self.assertIsNone(load_runbook("DiskPressure"))
