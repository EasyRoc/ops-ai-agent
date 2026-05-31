from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agent.channels import feishu


class _FailedResponse:
    def json(self):
        return {"code": 230001, "msg": "invalid receive_id"}


class _FailedClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, *args, **kwargs):
        return _FailedResponse()


class FeishuChannelTest(IsolatedAsyncioTestCase):
    async def test_send_message_raises_when_feishu_rejects_request(self):
        with patch.object(
            feishu, "_get_tenant_access_token", AsyncMock(return_value="token")
        ), patch.object(feishu.httpx, "AsyncClient", return_value=_FailedClient()):
            with self.assertRaisesRegex(RuntimeError, "invalid receive_id"):
                await feishu.send_message("chat_id", "oc_invalid", {})
