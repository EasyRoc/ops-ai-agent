import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent.channels.feishu import handle_card_action, verify_card_callback
from agent.db.crud import AsyncSessionLocal, get_incident, get_session, update_incident

logger = logging.getLogger("ops-agent.api.approvals")
router = APIRouter(prefix="/api/v1")


def parse_card_action(value) -> dict:
    """Parse Feishu button value, accepting both JSON strings and dicts."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return {}


@router.post("/approvals/callback")
async def approval_callback(request: Request, background_tasks: BackgroundTasks):
    """Handle Feishu interactive card callbacks for manual approval."""
    body = await request.json()
    headers = dict(request.headers)

    if body.get("challenge"):
        return {"challenge": body["challenge"]}

    if not await verify_card_callback(headers, body):
        logger.warning("收到非卡片审批回调: type=%s", body.get("type"))
        return {"status": "invalid_callback"}

    try:
        value = body.get("action", {}).get("value", {})
        action_value = parse_card_action(value)
    except json.JSONDecodeError:
        logger.warning("审批回调按钮值不是合法 JSON: %s", body.get("action"))
        return {"status": "invalid_action"}

    action = action_value.get("action")
    incident_id = action_value.get("incident_id")
    if not action or not incident_id:
        return {"status": "missing_params"}

    approval_status = await handle_card_action(action, incident_id)
    background_tasks.add_task(_update_incident_status, incident_id, approval_status)
    background_tasks.add_task(_update_feishu_card, body, incident_id, approval_status)

    return {
        "status": "ok",
        "incident_id": incident_id,
        "approval_status": approval_status,
    }


async def _update_incident_status(incident_id: str, approval_status: str) -> None:
    """Persist approval decision to the incident record."""
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
    logger.info(
        "审批回调已处理，等待后续接入飞书卡片更新: incident=%s, status=%s, open_id=%s",
        incident_id,
        approval_status,
        body.get("operator", {}).get("open_id"),
    )


@router.get("/incidents/{incident_id}/approval")
async def get_approval_status(
    incident_id: str,
    db: AsyncSession = Depends(get_session),
):
    """Return approval status for an incident."""
    incident = await get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    return {
        "incident_id": incident.id,
        "status": incident.status,
        "approval_status": incident.approval_status,
    }
