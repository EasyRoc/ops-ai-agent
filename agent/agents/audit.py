import logging

from agent.db.crud import AsyncSessionLocal, create_audit_log
from agent.db.models import AuditLog

logger = logging.getLogger("ops-agent.audit")


async def write_audit(
    incident_id: str | None,
    actor: str,
    action: str,
    detail: dict | None = None,
) -> None:
    """写入一条审计日志。

    审计日志是 Phase 3 自动执行链路的“黑匣子”：审批、执行、验证、报告都要留下证据。
    这里故意做成 best-effort，避免审计库偶发失败反过来阻塞告警处置主流程。
    """
    logger.info(
        "进入 write_audit: incident=%s, actor=%s, action=%s",
        incident_id or "-",
        actor,
        action,
    )
    try:
        async with AsyncSessionLocal() as session:
            await create_audit_log(
                session,
                AuditLog(
                    incident_id=incident_id,
                    actor=actor,
                    action=action,
                    detail=detail or {},
                ),
            )
        logger.info(
            "审计日志写入完成: incident=%s, actor=%s, action=%s",
            incident_id or "-",
            actor,
            action,
        )
    except Exception as exc:
        logger.error(
            "审计日志写入失败: incident=%s, actor=%s, action=%s, error=%s",
            incident_id or "-",
            actor,
            action,
            exc,
            exc_info=True,
        )
