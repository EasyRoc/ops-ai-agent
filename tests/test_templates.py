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


class FallbackCardTemplateTest(TestCase):
    def test_render_fallback_card_shows_ai_warning_and_buttons(self):
        card = render_card(
            "fallback_diagnosis_card",
            alert_title="[P2] order-service - DiskPressure",
            root_cause="磁盘使用率过高导致服务写入失败",
            ai_reasoning="根因分析显示磁盘 I/O 等待时间增加，结合 Pod 日志中的 'No space left on device' 错误，判断为磁盘空间不足。",
            action_plan="1. [低风险] 检查磁盘使用情况\n`kubectl get pods -n demo`",
            verify_condition="磁盘使用率 < 85%（指标: disk_usage, 操作符: <, 阈值: 85.0）",
            evidence_list="Pod 日志: No space left on device\n磁盘使用率: 92%",
            confidence="75",
            incident_id="INC-FALLBACK-01",
            duration="刚刚",
        )

        self.assertEqual(card["header"]["template"], "yellow")
        self.assertIn("AI 自主分析生成", card["elements"][0]["content"])
        self.assertIn("AI 推理过程", card["elements"][4]["content"])
        self.assertIn("磁盘 I/O", card["elements"][4]["content"])
        self.assertIn("验证条件", card["elements"][8]["content"])

        actions = card["elements"][14]["actions"]
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0]["value"]["action"], "approve_ai")
        self.assertEqual(actions[0]["text"]["content"], "AI 自动执行")
        self.assertEqual(actions[1]["value"]["action"], "manual_fix")
        self.assertEqual(actions[1]["text"]["content"], "我自己来")
        self.assertEqual(actions[2]["value"]["action"], "reject")
        self.assertEqual(actions[2]["text"]["content"], "拒绝")
        self.assertIn("AI 方案待确认", card["elements"][15]["elements"][0]["content"])

    def test_fallback_card_button_value_contains_incident_id(self):
        card = render_card(
            "fallback_diagnosis_card",
            alert_title="[P2] test - TestAlert",
            root_cause="test",
            ai_reasoning="test reasoning",
            action_plan="1. [低风险] test",
            verify_condition="CPU < 70%",
            evidence_list="test",
            confidence="50",
            incident_id="INC-TEST-123",
            duration="1m",
        )

        for action_item in card["elements"][14]["actions"]:
            self.assertEqual(action_item["value"]["incident_id"], "INC-TEST-123")


class RetryCardTemplateTest(TestCase):
    def test_render_retry_card_shows_round_and_buttons(self):
        card = render_card(
            "retry_card",
            alert_title="[P2] order-service - DiskPressure",
            retry_round="2",
            failure_reason="第 1 轮 kubectl delete pod 执行成功但 CPU 未恢复，可能 Pod 重建后仍调度到同一热点节点",
            retry_reasoning="分析了 Prometheus 指标和 Node 状态，发现 node-3 的 CPU 已满载。本轮改为扩容目标 Deployment 到其他节点。",
            action_plan="1. [中风险] 扩容 order-service 到其他节点\n`kubectl scale deployment order-service -n demo --replicas=4`",
            verify_condition="CPU 使用率 < 70%（指标: cpu, 操作符: <, 阈值: 70.0）",
            retry_history_summary="第 1 轮: 删除 Pod -> CPU 仍 85% -> 未恢复",
            incident_id="INC-RETRY-01",
        )

        self.assertEqual(card["header"]["template"], "orange")
        self.assertIn("第 2/5 轮重试", card["header"]["title"]["content"])
        self.assertIn("第 2 轮重试", card["elements"][0]["content"])
        self.assertIn("上轮失败原因", card["elements"][2]["content"])
        self.assertIn("kubectl delete pod", card["elements"][2]["content"])
        self.assertIn("自省分析", card["elements"][4]["content"])
        self.assertIn("node-3", card["elements"][4]["content"])

        actions = card["elements"][12]["actions"]
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["value"]["action"], "continue_retry")
        self.assertEqual(actions[0]["text"]["content"], "继续 AI 执行")
        self.assertEqual(actions[0]["value"]["round"], 2)
        self.assertEqual(actions[1]["value"]["action"], "stop_retry")
        self.assertEqual(actions[1]["text"]["content"], "转人工")
        self.assertIn("第 2/5 轮重试", card["elements"][13]["elements"][0]["content"])

    def test_retry_card_round3_shows_correct_count(self):
        card = render_card(
            "retry_card",
            alert_title="[P1] payment-service - ThreadPoolExhausted",
            retry_round="3",
            failure_reason="第 2 轮扩容未恢复，线程池仍满",
            retry_reasoning="检测到下游 inventory-service 响应超时导致线程堆积，本轮改为先重启下游服务再扩容",
            action_plan="1. [中风险] 重启 inventory-service\n`kubectl delete pod inventory-service-xyz -n demo`",
            verify_condition="错误率 < 2%",
            retry_history_summary="第 1 轮: 扩容 -> 失败\n第 2 轮: 扩容 + 清理 -> 失败",
            incident_id="INC-RETRY-02",
        )

        self.assertIn("第 3/5 轮重试", card["header"]["title"]["content"])
        self.assertEqual(card["elements"][12]["actions"][0]["value"]["round"], 3)
