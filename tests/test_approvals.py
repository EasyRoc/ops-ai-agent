from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks

from agent.api.v1.approvals import (
    _update_feishu_card,
    approval_callback,
    build_approval_result_card,
    extract_message_id,
    parse_card_action,
)
from agent.channels.feishu import handle_card_action, verify_card_callback


class _JSONRequest:
    def __init__(self, body):
        self._body = body
        self.headers = {}

    async def json(self):
        return self._body


class FeishuApprovalHelpersTest(IsolatedAsyncioTestCase):
    async def test_verify_card_callback_accepts_challenge_and_card_action(self):
        self.assertTrue(await verify_card_callback({}, {"challenge": "abc"}))
        self.assertTrue(await verify_card_callback({}, {"type": "card_action"}))
        self.assertFalse(await verify_card_callback({}, {"type": "event_callback"}))

    async def test_handle_card_action_maps_to_approval_status(self):
        self.assertEqual(await handle_card_action("approve", "INC-1"), "approved")
        self.assertEqual(await handle_card_action("reject", "INC-1"), "rejected")
        self.assertEqual(await handle_card_action("escalate", "INC-1"), "escalated")
        self.assertEqual(await handle_card_action("unknown", "INC-1"), "pending")

    async def test_ai_fallback_actions_map_to_approval_status(self):
        self.assertEqual(await handle_card_action("approve_ai", "INC-1"), "ai_approved")
        self.assertEqual(await handle_card_action("manual_fix", "INC-1"), "manual_executing")
        self.assertEqual(await handle_card_action("continue_retry", "INC-1"), "retry_continue")
        self.assertEqual(await handle_card_action("stop_retry", "INC-1"), "escalated")


class ApprovalCallbackTest(IsolatedAsyncioTestCase):
    async def test_callback_returns_challenge(self):
        response = await approval_callback(_JSONRequest({"challenge": "challenge-token"}), BackgroundTasks())

        self.assertEqual(response, {"challenge": "challenge-token"})

    async def test_callback_updates_incident_status_from_button_value(self):
        background_tasks = BackgroundTasks()

        with (
            patch("agent.api.v1.approvals._update_incident_status", new=AsyncMock()) as update_status,
            patch("agent.api.v1.approvals._update_feishu_card", new=AsyncMock()),
            patch("agent.api.v1.approvals._write_approval_audit", new=AsyncMock()),
            patch("agent.api.v1.approvals.run_execution_workflow", new=AsyncMock()) as run_workflow,
        ):
            response = await approval_callback(
                _JSONRequest(
                    {
                        "type": "card_action",
                        "action": {
                            "value": '{"action":"approve","incident_id":"INC-PHASE2"}',
                        },
                    }
                ),
                background_tasks,
            )
            for task in background_tasks.tasks:
                await task()

        self.assertEqual(response["approval_status"], "approved")
        update_status.assert_awaited_once_with("INC-PHASE2", "approved")
        run_workflow.assert_awaited_once()

    async def test_callback_accepts_new_feishu_card_action_event(self):
        background_tasks = BackgroundTasks()

        with (
            patch("agent.api.v1.approvals._update_incident_status", new=AsyncMock()) as update_status,
            patch("agent.api.v1.approvals._update_feishu_card", new=AsyncMock()),
            patch("agent.api.v1.approvals._write_approval_audit", new=AsyncMock()),
            patch("agent.api.v1.approvals.run_execution_workflow", new=AsyncMock()) as run_workflow,
        ):
            response = await approval_callback(
                _JSONRequest(
                    {
                        "schema": "2.0",
                        "header": {
                            "event_type": "card.action.trigger",
                        },
                        "event": {
                            "operator": {"open_id": "ou_test"},
                            "action": {
                                "value": {
                                    "action": "approve",
                                    "incident_id": "INC-NEW-CARD",
                                },
                            },
                        },
                    }
                ),
                background_tasks,
            )
            for task in background_tasks.tasks:
                await task()

        self.assertEqual(response["approval_status"], "approved")
        update_status.assert_awaited_once_with("INC-NEW-CARD", "approved")
        run_workflow.assert_awaited_once()

    async def test_approve_ai_triggers_execution_workflow(self):
        background_tasks = BackgroundTasks()

        with (
            patch("agent.api.v1.approvals._update_incident_status", new=AsyncMock()),
            patch("agent.api.v1.approvals._update_feishu_card", new=AsyncMock()),
            patch("agent.api.v1.approvals._write_approval_audit", new=AsyncMock()),
            patch("agent.api.v1.approvals.run_execution_workflow", new=AsyncMock()) as run_workflow,
        ):
            response = await approval_callback(
                _JSONRequest(
                    {
                        "type": "card_action",
                        "action": {
                            "value": '{"action":"approve_ai","incident_id":"INC-AI-01"}',
                        },
                    }
                ),
                background_tasks,
            )
            for task in background_tasks.tasks:
                await task()

        self.assertEqual(response["approval_status"], "ai_approved")
        run_workflow.assert_awaited_once()

    async def test_manual_fix_does_not_trigger_execution(self):
        background_tasks = BackgroundTasks()

        with (
            patch("agent.api.v1.approvals._update_incident_status", new=AsyncMock()),
            patch("agent.api.v1.approvals._update_feishu_card", new=AsyncMock()),
            patch("agent.api.v1.approvals._write_approval_audit", new=AsyncMock()),
            patch("agent.api.v1.approvals.run_execution_workflow", new=AsyncMock()) as run_workflow,
        ):
            response = await approval_callback(
                _JSONRequest(
                    {
                        "type": "card_action",
                        "action": {
                            "value": '{"action":"manual_fix","incident_id":"INC-MANUAL-01"}',
                        },
                    }
                ),
                background_tasks,
            )
            for task in background_tasks.tasks:
                await task()

        self.assertEqual(response["approval_status"], "manual_executing")
        run_workflow.assert_not_awaited()

    async def test_continue_retry_does_not_trigger_execution_in_phase_b(self):
        background_tasks = BackgroundTasks()

        with (
            patch("agent.api.v1.approvals._update_incident_status", new=AsyncMock()),
            patch("agent.api.v1.approvals._update_feishu_card", new=AsyncMock()),
            patch("agent.api.v1.approvals._write_approval_audit", new=AsyncMock()),
            patch("agent.api.v1.approvals.run_execution_workflow", new=AsyncMock()) as run_workflow,
        ):
            response = await approval_callback(
                _JSONRequest(
                    {
                        "type": "card_action",
                        "action": {
                            "value": '{"action":"continue_retry","incident_id":"INC-RETRY-01","round":2}',
                        },
                    }
                ),
                background_tasks,
            )
            for task in background_tasks.tasks:
                await task()

        self.assertEqual(response["approval_status"], "retry_continue")
        run_workflow.assert_not_awaited()

    async def test_update_feishu_card_replaces_buttons_with_approval_result(self):
        with patch("agent.channels.feishu.update_card", new=AsyncMock(return_value={"code": 0})) as update_card:
            await _update_feishu_card(
                {
                    "event": {
                        "operator": {"open_id": "ou_test"},
                        "context": {"open_message_id": "om_test_message"},
                    },
                },
                "INC-PHASE2",
                "approved",
            )

        update_card.assert_awaited_once()
        message_id, card = update_card.await_args.args
        self.assertEqual(message_id, "om_test_message")
        self.assertFalse(any(element.get("tag") == "action" for element in card["elements"]))
        self.assertIn("已批准", card["elements"][0]["content"])

    def test_approval_result_card_displays_ai_statuses(self):
        ai_card = build_approval_result_card("INC-AI-01", "ai_approved", "ou_test")
        manual_card = build_approval_result_card("INC-AI-02", "manual_executing", "ou_test")
        retry_card = build_approval_result_card("INC-AI-03", "retry_continue", "ou_test")

        self.assertIn("已批准 AI 自动执行", ai_card["elements"][0]["content"])
        self.assertIn("已转人工执行", manual_card["elements"][0]["content"])
        self.assertIn("已批准继续重试", retry_card["elements"][0]["content"])


class CardActionParserTest(TestCase):
    def test_parse_card_action_accepts_json_string_or_dict(self):
        self.assertEqual(
            parse_card_action('{"action":"reject","incident_id":"INC-1"}'),
            {"action": "reject", "incident_id": "INC-1"},
        )
        self.assertEqual(
            parse_card_action({"action": "escalate", "incident_id": "INC-2"}),
            {"action": "escalate", "incident_id": "INC-2"},
        )

    def test_extract_message_id_accepts_old_and_new_callback_payloads(self):
        self.assertEqual(
            extract_message_id({"open_message_id": "om_old"}),
            "om_old",
        )
        self.assertEqual(
            extract_message_id({"event": {"context": {"open_message_id": "om_new"}}}),
            "om_new",
        )
