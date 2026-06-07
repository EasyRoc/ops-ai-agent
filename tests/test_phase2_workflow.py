from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agent.agents.rca import _format_action_plan, _notify_diagnosis, analyze_root_cause
from agent.workflows.alert_workflow import build_alert_workflow


class Phase2WorkflowTest(IsolatedAsyncioTestCase):
    async def test_rca_adds_runbook_risk_and_pending_approval(self):
        state = {
            "alert_raw": {"alertname": "HighCPUUsage"},
            "incident_id": "INC-PHASE2",
            "alert_parsed": {
                "alertname": "HighCPUUsage",
                "service": "order-service",
                "env": "prod",
                "severity": "P1",
            },
            "context": {
                "metrics": {},
                "logs": [],
                "pods": {"total": 2, "ready": 2, "pods": [{"name": "order-service-abc"}]},
                "cmdb": {},
            },
            "diagnosis": None,
            "runbook": None,
            "risk_assessment": None,
            "approval_status": None,
            "error": None,
        }

        with (
            patch(
                "agent.agents.rca._diagnose_with_llm",
                new=AsyncMock(
                    return_value={
                        "root_cause": "CPU 异常升高",
                        "confidence": 0.8,
                        "evidence": ["CPU > 90%"],
                    }
                ),
            ),
            patch("agent.agents.rca._save_diagnosis", new=AsyncMock()),
            patch("agent.agents.rca._notify_diagnosis", new=AsyncMock()),
        ):
            result = await analyze_root_cause(state)

        self.assertEqual(result["approval_status"], "pending")
        self.assertEqual(result["runbook"]["name"], "cpu_high.md")
        self.assertEqual(result["risk_assessment"]["level"], "极高风险")
        self.assertTrue(result["risk_assessment"]["allowed"])
        self.assertTrue(any("kubectl scale deployment order-service" in step["command"] for step in result["runbook"]["steps"]))

    async def test_rca_uses_ai_fallback_when_runbook_not_matched(self):
        state = {
            "alert_raw": {"alertname": "UnmatchedDiskPressure"},
            "incident_id": "INC-AI-FALLBACK",
            "alert_parsed": {
                "alertname": "UnmatchedDiskPressure",
                "service": "order-service",
                "env": "prod",
                "severity": "P2",
            },
            "context": {
                "metrics": {
                    "cpu": {"current": 86.5},
                    "qps": {"current": 230.0},
                    "error_rate": {"current": 0.01},
                },
                "logs": [{"line": "disk pressure warning"}],
                "pods": {"total": 2, "ready": 2, "pods": [{"name": "order-service-abc"}]},
                "cmdb": {"dependencies": ["postgres"]},
            },
            "diagnosis": None,
            "runbook": None,
            "risk_assessment": None,
            "approval_status": None,
            "error": None,
        }
        ai_plan = {
            "name": "ai_fallback",
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "临时扩容 order-service，观察资源和 QPS 是否恢复",
                    "command": "kubectl scale deployment order-service -n demo --replicas=4",
                }
            ],
            "ai_generated": True,
            "ai_reasoning": "预置 Runbook 未覆盖该告警，结合 CPU 和 QPS 建议先做可回滚扩容。",
            "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0},
            "confidence": 0.72,
        }

        with (
            patch(
                "agent.agents.rca._diagnose_with_llm",
                new=AsyncMock(
                    return_value={
                        "root_cause": "磁盘压力伴随 CPU 升高",
                        "confidence": 0.7,
                        "evidence": ["disk pressure warning", "CPU 86.5%"],
                    }
                ),
            ),
            patch("agent.agents.fallback.generate_ai_action_plan", new=AsyncMock(return_value=ai_plan)) as fallback_mock,
            patch("agent.agents.rca._save_diagnosis", new=AsyncMock()),
            patch("agent.agents.rca._notify_diagnosis", new=AsyncMock()),
            patch("agent.agents.audit.write_audit", new=AsyncMock()) as write_audit,
        ):
            result = await analyze_root_cause(state)

        fallback_mock.assert_awaited_once()
        write_audit.assert_awaited_once()
        self.assertEqual(write_audit.await_args.args[2], "ai_plan_generated")
        self.assertEqual(result["approval_status"], "pending")
        self.assertEqual(result["runbook"]["name"], "ai_fallback")
        self.assertTrue(result["runbook"]["ai_generated"])
        self.assertTrue(result["risk_assessment"]["ai_generated"])
        self.assertEqual(result["risk_assessment"]["ai_confidence"], 0.72)
        self.assertTrue(result["risk_assessment"]["allowed"])
        self.assertIn("AI 自主生成方案", "\n".join(result["risk_assessment"]["warnings"]))

    def test_format_action_plan_marks_ai_generated_plan(self):
        text = _format_action_plan(
            {
                "name": "ai_fallback",
                "steps": [
                    {
                        "risk_level": "低风险",
                        "description": "查看 Pod 状态",
                        "command": "kubectl get pods -n demo -l app=order-service",
                    }
                ],
                "ai_generated": True,
                "ai_reasoning": "预置 Runbook 未命中，需要根据当前上下文生成低风险排查步骤。",
            }
        )

        self.assertIn("AI 自主分析方案", text)
        self.assertIn("预置 Runbook 未命中", text)
        self.assertIn("kubectl get pods", text)

    async def test_notify_diagnosis_routes_ai_plan_to_fallback_card(self):
        runbook = {
            "name": "ai_fallback",
            "ai_generated": True,
            "ai_reasoning": "预置 Runbook 未覆盖该告警，结合 CPU 和 QPS 建议先做可回滚扩容。",
            "verification": {
                "metric": "cpu",
                "operator": "<",
                "threshold": 70.0,
                "description": "CPU 使用率降至 70% 以下",
            },
            "steps": [
                {
                    "risk_level": "中风险",
                    "description": "临时扩容 order-service",
                    "command": "kubectl scale deployment order-service -n demo --replicas=4",
                }
            ],
        }

        with (
            patch("agent.tools.cmdb.get_service_chat_id", new=AsyncMock(return_value="oc_ai_chat")),
            patch("agent.channels.feishu.send_card_to_chat", new=AsyncMock(return_value={"code": 0})) as send_card,
        ):
            await _notify_diagnosis(
                "INC-AI-CARD",
                {"root_cause": "CPU 持续升高", "confidence": 0.72, "evidence": ["CPU 86.5%", "QPS 230"]},
                {"alertname": "UnmatchedDiskPressure", "service": "order-service", "severity": "P2"},
                runbook=runbook,
                risk_assessment={"level": "中风险", "score": 40, "warnings": ["AI 自主生成方案"]},
            )

        send_card.assert_awaited_once()
        chat_id, card = send_card.await_args.args
        self.assertEqual(chat_id, "oc_ai_chat")
        self.assertEqual(card["header"]["template"], "yellow")
        self.assertIn("AI 自主诊断", card["header"]["title"]["content"])
        self.assertIn("AI 自主分析生成", card["elements"][0]["content"])
        self.assertIn("CPU 使用率降至 70% 以下", card["elements"][8]["content"])
        self.assertEqual(card["elements"][14]["actions"][0]["value"]["action"], "approve_ai")
        self.assertEqual(card["elements"][14]["actions"][1]["value"]["action"], "manual_fix")

    async def test_workflow_preserves_phase2_state_fields(self):
        initial_state = {
            "alert_raw": {"service": "order-service", "alertname": "HighCPUUsage"},
            "incident_id": None,
            "alert_parsed": None,
            "context": None,
            "diagnosis": None,
            "runbook": None,
            "risk_assessment": None,
            "approval_status": None,
            "error": None,
        }

        async def parse(state):
            return {
                **state,
                "incident_id": "INC-PHASE2",
                "alert_parsed": {
                    "alertname": "HighCPUUsage",
                    "service": "order-service",
                    "env": "prod",
                    "severity": "P1",
                },
            }

        async def collect(state):
            return {**state, "context": {"pods": {"total": 2, "pods": []}}}

        async def diagnose(state):
            return {
                **state,
                "diagnosis": {"root_cause": "test"},
                "runbook": {"name": "cpu_high.md", "steps": []},
                "risk_assessment": {"level": "中风险", "allowed": True},
                "approval_status": "pending",
            }

        with (
            patch("agent.agents.alert.parse_and_create_incident", new=AsyncMock(side_effect=parse)),
            patch("agent.agents.supervisor.collect_context_for_incident", new=AsyncMock(side_effect=collect)),
            patch("agent.agents.rca.analyze_root_cause", new=AsyncMock(side_effect=diagnose)),
        ):
            result = await build_alert_workflow().ainvoke(initial_state)

        self.assertEqual(result["approval_status"], "pending")
        self.assertEqual(result["runbook"]["name"], "cpu_high.md")
