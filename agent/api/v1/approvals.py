import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agent.channels.feishu import handle_card_action, verify_card_callback
from agent.db.crud import AsyncSessionLocal, get_incident, get_session, update_incident
from agent.workflows.alert_workflow import AlertState, build_execution_workflow

logger = logging.getLogger("ops-agent.api.approvals")
router = APIRouter(prefix="/api/v1")

APPROVAL_DISPLAY = {
    "approved": ("已批准执行", "green"),
    "rejected": ("已拒绝", "red"),
    "escalated": ("已转人工", "orange"),
    "pending": ("待审批", "blue"),
    "ai_approved": ("已批准 AI 自动执行", "green"),
    "manual_executing": ("已转人工执行，请手动执行方案中的命令", "blue"),
    "retry_continue": ("已批准继续重试", "orange"),
}


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


def extract_message_id(body: dict) -> str:
    """Extract Feishu message id from card callback payloads."""
    event = body.get("event", {})
    context = event.get("context", {})
    message_id = (
        body.get("open_message_id")
        or body.get("message_id")
        or context.get("open_message_id")
        or context.get("message_id")
        or event.get("open_message_id")
        or event.get("message_id")
    )
    logger.info("提取飞书消息ID: message_id=%s", message_id or "-")
    return message_id or ""


def extract_operator(body: dict) -> str:
    """Extract a readable operator id from old and new Feishu callback payloads."""
    operator = body.get("operator") or body.get("event", {}).get("operator", {})
    operator_id = (
        operator.get("name")
        or operator.get("open_id")
        or operator.get("user_id")
        or operator.get("union_id")
    )
    logger.info("提取飞书审批操作人: operator=%s", operator_id or "-")
    return operator_id or "未知"


def build_approval_result_card(incident_id: str, approval_status: str, operator: str) -> dict:
    """Build a result card without any action buttons."""
    from agent.templates import render_card

    approval_text, result_color = APPROVAL_DISPLAY.get(
        approval_status,
        (approval_status or "未知", "blue"),
    )
    logger.info(
        "构建审批结果卡片: incident=%s, approval_status=%s, operator=%s",
        incident_id,
        approval_status,
        operator,
    )
    return render_card(
        "approval_result_card",
        incident_id=incident_id,
        result_color=result_color,
        approval_text=approval_text,
        operator=operator,
        comment="审批状态已写入 Incident，原操作按钮已失效。",
    )


@router.post("/approvals/callback")
async def approval_callback(request: Request, background_tasks: BackgroundTasks):
    """Handle Feishu interactive card callbacks for manual approval."""
    body = await request.json()
    headers = dict(request.headers)
    logger.info("进入 approval_callback: 飞书卡片审批回调, headers=%s, body=%s", headers, body)
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
    operator = extract_operator(body)

    # 将审批结果写入 Incident 表（status、approval_status）
    background_tasks.add_task(_update_incident_status, incident_id, approval_status)

    # 把飞书原卡片替换为审批结果卡片，去掉按钮避免重复操作
    background_tasks.add_task(_update_feishu_card, body, incident_id, approval_status)

    # 审计日志：记录谁在什么时间做了什么审批操作
    background_tasks.add_task(
        _write_approval_audit,
        incident_id,
        operator,
        approval_status,
        {"action": action, "callback_type": callback_type},
    )

    # 审批通过后触发 Phase 3 执行工作流（execute → verify → report）。
    # AI 兜底卡片的“AI 自动执行”复用同一个执行入口；重试类动作留给 Phase C 专用工作流处理。
    if approval_status in {"approved", "ai_approved"}:
        background_tasks.add_task(run_execution_workflow, incident_id, body)

    logger.info(
        "审批回调后台任务已入队: incident=%s, approval_status=%s, operator=%s",
        incident_id,
        approval_status,
        operator,
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


async def _write_approval_audit(incident_id: str, operator: str, approval_status: str, detail: dict) -> None:
    """Record the Feishu approval decision into audit_logs."""
    from agent.agents.audit import write_audit

    logger.info(
        "进入 _write_approval_audit: incident=%s, operator=%s, status=%s",
        incident_id,
        operator,
        approval_status,
    )
    await write_audit(incident_id, operator, approval_status, detail)


async def _load_execution_state(incident_id: str, operator: str) -> AlertState | None:
    """从数据库恢复 Phase 3 工作流需要的最小状态。

    审批回调是无状态 HTTP 请求，不能直接拿到 diagnose 阶段的内存 state。
    所以这里从 incidents 表恢复 alert、diagnosis、runbook、risk 这些关键字段。
    """
    logger.info("进入 _load_execution_state: incident=%s, operator=%s", incident_id, operator)
    async with AsyncSessionLocal() as session:
        incident = await get_incident(session, incident_id)

    if not incident:
        logger.warning("恢复执行工作流状态失败，工单不存在: incident=%s", incident_id)
        return None

    state: AlertState = {
        "alert_raw": {},
        "incident_id": incident.id,
        "alert_parsed": {
            "alertname": incident.alert_name or "",
            "service": incident.service,
            "env": incident.env,
            "severity": incident.severity,
            "value": incident.alert_value or "",
        },
        "duplicate_alert": None,
        "context": {
            "service": incident.service,
            "env": incident.env,
        },
        "diagnosis": {
            "root_cause": incident.root_cause or "未记录根因",
            "confidence": incident.confidence or 0,
            "evidence": incident.evidence or [],
        },
        "runbook": {
            "name": incident.runbook_name,
            "steps": incident.action_plan or [],
        },
        "risk_assessment": incident.risk_assessment or {},
        "approval_status": incident.approval_status,
        "execution_result": None,
        "verification_result": None,
        "report": None,
        "operator": operator,
        "error": None,
    }
    logger.info(
        "执行工作流状态已恢复: incident=%s, service=%s, runbook=%s, steps=%s, risk_allowed=%s",
        incident.id,
        incident.service,
        incident.runbook_name,
        len(incident.action_plan or []),
        (incident.risk_assessment or {}).get("allowed"),
    )
    return state


async def run_execution_workflow(incident_id: str, body: dict | None = None) -> dict:
    """审批通过后启动 Phase 3: execute → verify → report。"""
    operator = extract_operator(body or {})
    logger.info(
        "进入 run_execution_workflow: incident=%s, operator=%s",
        incident_id,
        operator,
    )
    state = await _load_execution_state(incident_id, operator)
    if not state:
        return {"status": "not_found", "incident_id": incident_id}

    workflow = build_execution_workflow()
    try:
        result = await workflow.ainvoke(state)
        logger.info(
            "Phase 3 执行工作流完成: incident=%s, execution=%s, verification=%s, has_report=%s",
            incident_id,
            (result.get("execution_result") or {}).get("status"),
            (result.get("verification_result") or {}).get("status"),
            bool(result.get("report")),
        )
        return result
    except Exception as exc:
        logger.error(
            "Phase 3 执行工作流失败: incident=%s, error=%s",
            incident_id,
            exc,
            exc_info=True,
        )
        async with AsyncSessionLocal() as session:
            await update_incident(
                session,
                incident_id,
                status="escalated",
                approval_status="escalated",
            )
        return {"status": "failed", "incident_id": incident_id, "error": str(exc)}


async def _update_feishu_card(body: dict, incident_id: str, approval_status: str) -> None:
    """Replace the original interactive card with an approval result card."""
    from agent.channels.feishu import update_card

    logger.info("进入 _update_feishu_card: incident=%s, status=%s", incident_id, approval_status)
    message_id = extract_message_id(body)
    if not message_id:
        logger.warning(
            "无法更新飞书原卡片，回调中缺少消息ID: incident=%s, status=%s",
            incident_id,
            approval_status,
        )
        return

    operator = extract_operator(body)
    card = build_approval_result_card(incident_id, approval_status, operator)
    try:
        result = await update_card(message_id, card)
    except Exception as exc:
        logger.error(
            "飞书原卡片更新异常: incident=%s, message_id=%s, error=%s",
            incident_id,
            message_id,
            exc,
            exc_info=True,
        )
        return

    logger.info(
        "飞书原卡片已更新为审批结果: incident=%s, status=%s, message_id=%s, code=%s",
        incident_id,
        approval_status,
        message_id,
        result.get("code"),
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
