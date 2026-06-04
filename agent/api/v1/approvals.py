import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent.channels.feishu import handle_card_action, verify_card_callback
from agent.db.crud import AsyncSessionLocal, get_incident, get_session, update_incident

logger = logging.getLogger("ops-agent.api.approvals")
router = APIRouter(prefix="/api/v1")


def _callback_type(body: dict) -> str:
    if body.get("challenge"):
        return "challenge"
    if body.get("type"):
        return body.get("type")
    return body.get("header", {}).get("event_type", "unknown")


def parse_card_action(value) -> dict:
    """Parse Feishu button value, accepting both JSON strings and dicts."""
    logger.info("进入 parse_card_action: value_type=%s", type(value).__name__)
    if isinstance(value, dict):
        logger.info("parse_card_action 完成: dict_keys=%s", list(value.keys()))
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        logger.info("parse_card_action 完成: json_keys=%s", list(parsed.keys()))
        return parsed
    logger.warning("parse_card_action 收到不支持的值类型: value_type=%s", type(value).__name__)
    return {}


def extract_card_action_value(body: dict) -> dict:
    """Extract action value from old and new Feishu card callback payloads."""
    callback_type = _callback_type(body)
    logger.info("进入 extract_card_action_value: callback_type=%s", callback_type)
    if body.get("type") == "card_action":
        logger.info("识别旧版飞书卡片回调: type=card_action")
        return body.get("action", {}).get("value", {})
    if body.get("header", {}).get("event_type") == "card.action.trigger":
        logger.info("识别新版飞书卡片回调: event_type=card.action.trigger")
        return body.get("event", {}).get("action", {}).get("value", {})
    logger.warning("未识别的飞书卡片回调结构: callback_type=%s", callback_type)
    return {}


@router.post("/approvals/callback")
async def approval_callback(request: Request, background_tasks: BackgroundTasks):
    """Handle Feishu interactive card callbacks for manual approval."""
    logger.info("进入 approval_callback: 飞书卡片审批回调")
    body = await request.json()
    headers = dict(request.headers)
    callback_type = _callback_type(body)
    logger.info(
        "收到飞书审批回调: callback_type=%s, content_type=%s, has_challenge=%s",
        callback_type,
        headers.get("content-type", ""),
        bool(body.get("challenge")),
    )

    if body.get("challenge"):
        logger.info("处理飞书回调地址校验: challenge=%s", body["challenge"])
        return {"challenge": body["challenge"]}

    if not await verify_card_callback(headers, body):
        logger.warning("收到非卡片审批回调: callback_type=%s", callback_type)
        return {"status": "invalid_callback"}

    try:
        action_value = parse_card_action(extract_card_action_value(body))
    except json.JSONDecodeError:
        logger.warning("审批回调按钮值不是合法 JSON: callback_type=%s", callback_type)
        return {"status": "invalid_action"}

    action = action_value.get("action")
    incident_id = action_value.get("incident_id")
    logger.info(
        "审批回调动作解析完成: incident=%s, action=%s, callback_type=%s",
        incident_id or "-",
        action or "-",
        callback_type,
    )
    if not action or not incident_id:
        logger.warning(
            "审批回调缺少必要参数: incident=%s, action=%s",
            incident_id or "-",
            action or "-",
        )
        return {"status": "missing_params"}

    approval_status = await handle_card_action(action, incident_id)
    background_tasks.add_task(_update_incident_status, incident_id, approval_status)
    background_tasks.add_task(_update_feishu_card, body, incident_id, approval_status)
    logger.info(
        "审批回调后台任务已入队: incident=%s, approval_status=%s",
        incident_id,
        approval_status,
    )

    return {
        "status": "ok",
        "incident_id": incident_id,
        "approval_status": approval_status,
    }


async def _update_incident_status(incident_id: str, approval_status: str) -> None:
    """Persist approval decision to the incident record."""
    logger.info("进入 _update_incident_status: incident=%s, status=%s", incident_id, approval_status)
    async with AsyncSessionLocal() as session:
        incident = await update_incident(
            session,
            incident_id,
            status=approval_status,
            approval_status=approval_status,
        )
        if incident:
            logger.info("审批状态已更新: incident=%s, status=%s", incident_id, approval_status)
        else:
            logger.warning("审批状态更新失败，工单不存在: incident=%s", incident_id)


async def _update_feishu_card(body: dict, incident_id: str, approval_status: str) -> None:
    """Placeholder for updating the original Feishu card after approval."""
    logger.info("进入 _update_feishu_card: incident=%s, status=%s", incident_id, approval_status)
    logger.info(
        "审批回调已处理，等待后续接入飞书卡片更新: incident=%s, status=%s, open_id=%s",
        incident_id,
        approval_status,
        body.get("operator", {}).get("open_id") or body.get("event", {}).get("operator", {}).get("open_id"),
    )


@router.get("/incidents/{incident_id}/approval")
async def get_approval_status(
    incident_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Return approval status for an incident."""
    logger.info("进入 get_approval_status: incident=%s", incident_id)
    incident = await get_incident(db, incident_id)
    if not incident:
        logger.warning("查询审批状态失败，工单不存在: incident=%s", incident_id)
        raise HTTPException(status_code=404, detail="Incident not found")

    logger.info(
        "查询审批状态完成: incident=%s, status=%s, approval_status=%s",
        incident.id,
        incident.status,
        incident.approval_status,
    )
    return {
        "incident_id": incident.id,
        "status": incident.status,
        "approval_status": incident.approval_status,
    }
