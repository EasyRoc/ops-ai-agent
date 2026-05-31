from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select

from agent.config import settings
from agent.db.models import Incident, Execution, Report, AuditLog

engine = create_async_engine(settings.database_url)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def create_incident(session: AsyncSession, incident: Incident) -> Incident:
    session.add(incident)
    await session.commit()
    await session.refresh(incident)
    return incident


async def get_incident(session: AsyncSession, incident_id: str) -> Incident | None:
    result = await session.execute(
        select(Incident).where(Incident.id == incident_id)
    )
    return result.scalar_one_or_none()


async def update_incident(session: AsyncSession, incident_id: str, **kwargs) -> Incident | None:
    incident = await get_incident(session, incident_id)
    if incident:
        for key, value in kwargs.items():
            setattr(incident, key, value)
        await session.commit()
        await session.refresh(incident)
    return incident


async def list_incidents(session: AsyncSession, status: str = None, limit: int = 50) -> list[Incident]:
    stmt = select(Incident).order_by(Incident.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Incident.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_audit_log(session: AsyncSession, log: AuditLog) -> AuditLog:
    session.add(log)
    await session.commit()
    return log
