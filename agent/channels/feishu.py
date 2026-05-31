# agent/channels/feishu.py
import logging
import httpx

from agent.config import settings

logger = logging.getLogger("ops-agent.feishu")

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
FEISHU_AUTH_URL = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
FEISHU_MESSAGE_URL = f"{FEISHU_API_BASE}/im/v1/messages"
FEISHU_CHAT_URL = f"{FEISHU_API_BASE}/im/v1/chats"

_token_cache: dict = {"token": None, "expires_at": 0}


async def _get_tenant_access_token() -> str:
    import time
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FEISHU_AUTH_URL,
            json={
                "app_id": settings.feishu_app_id,
                "app_secret": settings.feishu_app_secret,
            },
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"Feishu auth failed: {data}")

        _token_cache["token"] = data["tenant_access_token"]
        _token_cache["expires_at"] = now + data.get("expire", 7200)
        return _token_cache["token"]


async def send_message(receive_id_type: str, receive_id: str, content: dict) -> dict:
    """Send a Feishu message"""
    token = await _get_tenant_access_token()
    body = {
        "receive_id": receive_id,
        "msg_type": content.get("msg_type", "interactive"),
        "content": content.get("content", "{}"),
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{FEISHU_MESSAGE_URL}?receive_id_type={receive_id_type}",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        result = resp.json()
        if result.get("code") != 0:
            logger.error(f"Feishu send failed: {result}")
        return result


async def send_card_to_chat(chat_id: str, card: dict) -> dict:
    """Send interactive card to Feishu group chat"""
    import json
    return await send_message("chat_id", chat_id, {
        "msg_type": "interactive",
        "content": json.dumps(card),
    })


async def update_card(message_id: str, card: dict) -> dict:
    """Update an existing card message"""
    token = await _get_tenant_access_token()
    import json

    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{FEISHU_API_BASE}/im/v1/messages/{message_id}",
            json={"content": json.dumps(card)},
            headers={"Authorization": f"Bearer {token}"},
        )
        return resp.json()
