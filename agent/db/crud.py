import logging
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select

from agent.config import settings
from agent.db.models import Incident, Execution, Report, AuditLog

logger = logging.getLogger("ops-agent.db.crud")

engine = create_async_engine(settings.database_url)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def create_incident(session: AsyncSession, incident: Incident) -> Incident:
    logger.info(f"创建工单: 服务={incident.service}, 告警={incident.alert_name}, 级别={incident.severity}")
    session.add(incident)
    await session.commit()
    await session.refresh(incident)
    logger.info(f"工单已创建: id={incident.id}")
    return incident


async def get_incident(session: AsyncSession, incident_id: str) -> Incident | None:
    result = await session.execute(
        select(Incident).where(Incident.id == incident_id)
    )
    incident = result.scalar_one_or_none()
    if incident:
        logger.debug(f"工单已找到: id={incident_id}")
    else:
        logger.warning(f"工单未找到: id={incident_id}")
    return incident


async def update_incident(session: AsyncSession, incident_id: str, **kwargs) -> Incident | None:
    incident = await get_incident(session, incident_id)
    if incident:
        for key, value in kwargs.items():
            setattr(incident, key, value)
        await session.commit()
        await session.refresh(incident)
        logger.info(f"工单已更新: id={incident_id}, 字段={list(kwargs.keys())}")
    return incident


async def list_incidents(session: AsyncSession, status: str = None, limit: int = 50) -> list[Incident]:
    stmt = select(Incident).order_by(Incident.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Incident.status == status)
    result = await session.execute(stmt)
    incidents = list(result.scalars().all())
    logger.info(f"查询工单列表: 状态={status or '全部'}, 限制={limit}, 结果={len(incidents)} 条")
    return incidents


async def create_audit_log(session: AsyncSession, log: AuditLog) -> AuditLog:
    logger.info(f"创建审计日志: incident_id={log.incident_id}, 操作={log.action}")
    session.add(log)
    await session.commit()
    return log
