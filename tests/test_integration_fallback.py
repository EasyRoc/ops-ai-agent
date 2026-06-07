"""集成测试：RCA 诊断 -> AI Fallback 方案生成 -> 风险评估。"""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch


class RcaToFallbackIntegrationTest(IsolatedAsyncioTestCase):
    """覆盖 rca.py 在预置 Runbook 未命中时的真实跨模块调用。"""

    async def test_build_action_plan_falls_back_to_ai_when_runbook_is_none(self):
        from agent.agents.rca import _build_action_plan

        with (
            patch("agent.agents.runbook.load_runbook", return_value=None),
            patch(
                "agent.agents.fallback.chat_json",
                new=AsyncMock(
                    return_value={
                        "reasoning": "磁盘 I/O 等待升高，先用可回滚扩容分摊负载",
                        "steps": [
                            {
                                "risk_level": "中风险",
                                "description": "扩容 order-service 分摊负载",
                                "command": "kubectl scale deployment order-service -n demo --replicas=4",
                            }
                        ],
                        "rollback": "kubectl rollout undo deployment order-service -n demo",
                        "verification": {
                            "metric": "cpu",
                            "operator": "<",
                            "threshold": 70.0,
                            "description": "CPU 降至 70% 以下",
                        },
                        "confidence": 0.75,
                    }
                ),
            ),
        ):
            runbook, risk = await _build_action_plan(
                context={
                    "metrics": {"cpu": {"current": 92.5}},
                    "logs": [{"line": "No space left on device"}],
                    "pods": {"total": 3, "ready": 2},
                    "cmdb": {"owner": "ops-team"},
                },
                alert={
                    "alertname": "DiskPressure",
                    "service": "order-service",
                    "env": "prod",
                    "severity": "P2",
                    "value": "92%",
                },
                diagnosis={"root_cause": "磁盘空间不足", "confidence": 0.7, "evidence": ["磁盘 92%"]},
            )

        self.assertIsNotNone(runbook)
        self.assertTrue(runbook["ai_generated"])
        self.assertEqual(runbook["name"], "ai_fallback")
        self.assertEqual(runbook["steps"][0]["risk_level"], "中风险")
        self.assertTrue(risk["ai_generated"])
        self.assertEqual(risk["ai_confidence"], 0.75)
        self.assertIn("AI 自主生成方案", "\n".join(risk["warnings"]))

    async def test_ai_fallback_preserves_reasoning_in_formatted_output(self):
        from agent.agents.rca import _build_action_plan, _format_action_plan

        ai_reasoning = "根据 Prometheus 指标和 Loki 日志综合分析，node-3 的磁盘 I/O 已满载"
        with (
            patch("agent.agents.runbook.load_runbook", return_value=None),
            patch(
                "agent.agents.fallback.chat_json",
                new=AsyncMock(
                    return_value={
                        "reasoning": ai_reasoning,
                        "steps": [
                            {
                                "risk_level": "低风险",
                                "description": "查看 Pod 状态",
                                "command": "kubectl get pods -n demo",
                            }
                        ],
                        "rollback": "",
                        "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
                        "confidence": 0.6,
                    }
                ),
            ),
        ):
            runbook, _risk = await _build_action_plan(
                context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
                alert={"alertname": "DiskPressure", "service": "order-service", "env": "prod", "severity": "P2"},
                diagnosis={"root_cause": "磁盘满", "confidence": 0.5, "evidence": []},
            )

        text = _format_action_plan(runbook)

        self.assertIn(ai_reasoning[:30], runbook["ai_reasoning"])
        self.assertIn("AI 自主分析方案", text)
        self.assertIn(ai_reasoning[:30], text)

    async def test_ai_high_risk_plan_is_auto_blocked(self):
        from agent.agents.rca import _build_action_plan

        with (
            patch("agent.agents.runbook.load_runbook", return_value=None),
            patch(
                "agent.agents.fallback.chat_json",
                new=AsyncMock(
                    return_value={
                        "reasoning": "需要重启异常 Pod 释放资源，但生产核心链路风险较高",
                        "steps": [
                            {
                                "risk_level": "高风险",
                                "description": "重启 payment-service 异常 Pod",
                                "command": "kubectl delete pod payment-service-0 -n demo",
                            }
                        ],
                        "rollback": "",
                        "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
                        "confidence": 0.5,
                    }
                ),
            ),
        ):
            _runbook, risk = await _build_action_plan(
                context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
                alert={"alertname": "DiskFull", "service": "payment-service", "env": "prod", "severity": "P1"},
                diagnosis={"root_cause": "磁盘满", "confidence": 0.8, "evidence": []},
            )

        self.assertIn(risk["level"], {"高风险", "极高风险"})
        self.assertFalse(risk["allowed"])
        self.assertIn("AI 自评高风险", "\n".join(risk["warnings"]))


class FallbackToRiskIntegrationTest(IsolatedAsyncioTestCase):
    """覆盖 fallback 输出经过 risk 模块评估后的关键状态。"""

    async def test_ai_plan_risk_includes_ai_metadata(self):
        from agent.agents.fallback import generate_ai_action_plan
        from agent.agents.risk import evaluate_risk
        from agent.agents.runbook import ActionStep

        with patch(
            "agent.agents.fallback.chat_json",
            new=AsyncMock(
                return_value={
                    "reasoning": "流量上涨导致 CPU 高",
                    "steps": [
                        {
                            "risk_level": "中风险",
                            "description": "扩容 order-service",
                            "command": "kubectl scale deployment order-service -n demo --replicas=4",
                        }
                    ],
                    "rollback": "kubectl rollout undo deployment order-service -n demo",
                    "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0, "description": "CPU < 70%"},
                    "confidence": 0.8,
                }
            ),
        ):
            ai_plan = await generate_ai_action_plan(
                context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
                alert={"alertname": "HighCPUUsage", "service": "inventory-service", "env": "prod", "severity": "P2"},
                diagnosis={"root_cause": "CPU 高", "confidence": 0.7, "evidence": ["CPU spike"]},
            )

        ai_steps = [
            ActionStep(
                risk_level=step.get("risk_level", "中风险"),
                description=step.get("description", ""),
                command=step.get("command", ""),
            )
            for step in ai_plan["steps"]
        ]
        risk = evaluate_risk(ai_steps, "P2", "inventory-service", "prod")
        risk["ai_generated"] = True
        risk["ai_confidence"] = ai_plan.get("confidence", 0.5)
        risk["ai_reasoning"] = ai_plan.get("ai_reasoning", "")

        self.assertTrue(ai_plan["ai_generated"])
        self.assertEqual(risk["ai_confidence"], 0.8)
        self.assertIn("流量上涨", risk["ai_reasoning"])
        self.assertTrue(risk["ai_generated"])
        self.assertTrue(risk["allowed"])

    async def test_validation_rejects_command_outside_whitelist(self):
        from agent.agents.fallback import generate_ai_action_plan

        with patch(
            "agent.agents.fallback.chat_json",
            new=AsyncMock(
                return_value={
                    "reasoning": "需要删除 namespace 清理资源",
                    "steps": [
                        {
                            "risk_level": "高风险",
                            "description": "删除整个 namespace",
                            "command": "kubectl delete namespace demo",
                        }
                    ],
                    "rollback": "",
                    "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
                    "confidence": 0.3,
                }
            ),
        ):
            result = await generate_ai_action_plan(
                context={"metrics": {}, "logs": [], "pods": {}, "cmdb": {}},
                alert={"alertname": "Test", "service": "test", "env": "prod", "severity": "P3", "value": ""},
                diagnosis={"root_cause": "test", "confidence": 0.5, "evidence": []},
            )

        self.assertIsNone(result)
