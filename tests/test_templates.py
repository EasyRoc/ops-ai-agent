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
            evidence_list=evidence,
            confidence="40",
            incident_id="INC-TEST",
            status="待确认",
            duration="刚刚",
        )

        self.assertEqual(card["elements"][0]["content"], '**根因判断：**\nCPU "hot"')
        self.assertEqual(card["elements"][2]["content"], f"**证据：**\n{evidence}")
