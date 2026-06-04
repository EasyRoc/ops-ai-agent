from unittest import TestCase

from agent.templates import render_card


class CardTemplateTest(TestCase):
    def test_render_card_escapes_multiline_dynamic_text(self):
        evidence = 'CPU usage is high\npod="order-service-0"'

        card = render_card(
            "diagnosis_card",
            alert_title="[P2] order-service - HighCPUUsage",
            severity_color="yellow",
            root_cause='CPU "hot"',
            action_plan='1. [中风险] 扩容 order-service\n`kubectl scale deployment order-service -n demo --replicas=4`',
            risk_level="中风险",
            risk_score="40",
            risk_warnings="核心服务 order-service，影响范围较大",
            evidence_list=evidence,
            confidence="40",
            incident_id="INC-TEST",
            status="待确认",
            duration="刚刚",
        )

        self.assertEqual(card["elements"][0]["content"], '**根因判断：**\nCPU "hot"')
        self.assertIn("扩容 order-service", card["elements"][2]["content"])
        self.assertEqual(card["elements"][6]["content"], f"**证据：**\n{evidence}")
        self.assertEqual(card["elements"][10]["tag"], "action")
        self.assertEqual(card["elements"][10]["actions"][0]["value"]["action"], "approve")
        self.assertEqual(card["elements"][10]["actions"][0]["value"]["incident_id"], "INC-TEST")
