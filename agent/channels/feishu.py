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
        logger.debug("飞书 Token: 使用缓存")
        return _token_cache["token"]

    logger.info("飞书 Token: 请求新的 tenant_access_token")
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
            logger.error(f"飞书认证失败: code={data.get('code')}, msg={data.get('msg')}")
            raise Exception(f"Feishu auth failed: {data}")

        _token_cache["token"] = data["tenant_access_token"]
        _token_cache["expires_at"] = now + data.get("expire", 7200)
        logger.info(f"飞书 Token: 已获取, 有效期={data.get('expire', 7200)}秒")
        return _token_cache["token"]


async def send_message(receive_id_type: str, receive_id: str, content: dict) -> dict:
    """Send a Feishu message"""
    token = await _get_tenant_access_token()
    msg_type = content.get("msg_type", "interactive")
    body = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": content.get("content", "{}"),
    }

    logger.info(f"飞书 发送消息: 类型={msg_type}, 接收方类型={receive_id_type}")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{FEISHU_MESSAGE_URL}?receive_id_type={receive_id_type}",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        result = resp.json()
        if result.get("code") != 0:
            logger.error(f"飞书 发送消息失败: code={result.get('code')}, msg={result.get('msg')}")
            raise RuntimeError(f"Feishu send failed: {result}")
        logger.info(f"飞书 发送消息成功: message_id={result.get('data', {}).get('message_id', '?')}")
        return result


async def send_card_to_chat(chat_id: str, card: dict) -> dict:
    """Send interactive card to Feishu group chat"""
    import json
    logger.info(f"飞书 发送卡片到群: chat_id={chat_id}")
    return await send_message("chat_id", chat_id, {
        "msg_type": "interactive",
        "content": json.dumps(card),
    })


async def update_card(message_id: str, card: dict) -> dict:
    """Update an existing card message"""
    token = await _get_tenant_access_token()
    import json

    logger.info(f"飞书 更新卡片: message_id={message_id}")
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{FEISHU_API_BASE}/im/v1/messages/{message_id}",
            json={"content": json.dumps(card)},
            headers={"Authorization": f"Bearer {token}"},
        )
        result = resp.json()
        if result.get("code") != 0:
            logger.error(f"飞书 更新卡片失败: code={result.get('code')}, msg={result.get('msg')}")
        else:
            logger.info(f"飞书 更新卡片成功: message_id={message_id}")
        return result
