import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.crud import AsyncSessionLocal
from agent.db.models import AuditLog

logger = logging.getLogger("ops-agent.api.audit")
router = APIRouter(prefix="/api/v1")


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/incidents/{incident_id}/audit")
async def list_audit_logs_endpoint(
    incident_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """查询某个工单的审计日志，按时间正序返回给 Web Console 时间线使用。"""
    logger.info("进入 list_audit_logs_endpoint: incident=%s, limit=%s", incident_id, limit)
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.incident_id == incident_id)
        .order_by(AuditLog.created_at.asc())
        .limit(limit)
    )
    logs = list(result.scalars().all())
    logger.info("审计日志查询完成: incident=%s, count=%s", incident_id, len(logs))
    return {
        "incident_id": incident_id,
        "total": len(logs),
        "audit_logs": [
            {
                "id": log.id,
                "actor": log.actor,
                "action": log.action,
                "detail": log.detail,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }
