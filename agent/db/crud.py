import logging
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, text

from agent.config import settings
from agent.db.models import Incident, Execution, Report, AuditLog

logger = logging.getLogger("ops-agent.db.crud")

engine = create_async_engine(settings.database_url)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def ensure_phase2_schema() -> None:
    """Apply lightweight idempotent schema additions for existing local databases."""
    statements = [
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS runbook_name VARCHAR(128)",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS action_plan JSONB",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS risk_assessment JSONB",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS approval_status VARCHAR(32)",
    ]
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
    logger.info("Phase 2 schema ensured")


async def ensure_phase3_schema() -> None:
    """Apply idempotent schema additions for Phase 3 auto-execution features."""
    statements = [
        "ALTER TABLE reports ADD COLUMN IF NOT EXISTS fault_patterns JSONB",
        "ALTER TABLE executions ALTER COLUMN action TYPE VARCHAR(512)",
        "CREATE INDEX IF NOT EXISTS idx_reports_incident ON reports(incident_id)",
    ]
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
    logger.info("Phase 3 schema ensured")


async def ensure_phase4_schema() -> None:
    """为 Phase D 可观测性能力补齐幂等 schema 变更。"""
    statements = [
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retry_history JSONB",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_generated BOOLEAN DEFAULT FALSE",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_reasoning TEXT",
        "ALTER TABLE executions ADD COLUMN IF NOT EXISTS round INTEGER DEFAULT 1",
        "ALTER TABLE executions ADD COLUMN IF NOT EXISTS ai_analysis TEXT",
        "CREATE INDEX IF NOT EXISTS idx_incidents_ai_generated ON incidents(ai_generated)",
        "CREATE INDEX IF NOT EXISTS idx_executions_incident_round ON executions(incident_id, round)",
    ]
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
    logger.info("Phase 4 schema ensured")


async def migrate_retry_data() -> int:
    """把 Phase C 暂存在 risk_assessment.retry 的数据迁移到专用列。

    迁移逻辑在启动时执行，必须保持幂等：只有专用列还没有重试轮次、
    且工单已经标记为 AI 兜底时，才尝试从旧 JSON 结构中恢复。
    返回迁移条数，便于测试和启动日志观察。
    """
    logger.info("开始迁移 risk_assessment.retry 到专用列")
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Incident).where(
                Incident.retry_count == 0,
                Incident.ai_generated == True,  # noqa: E712 - SQLAlchemy 需要生成 IS TRUE 条件
            )
        )
        incidents = list(result.scalars().all())
        migrated = 0
        for incident in incidents:
            risk = incident.risk_assessment or {}
            retry_meta = risk.get("retry") or {}
            if not retry_meta:
                logger.info("跳过无旧重试数据的工单: incident=%s", incident.id)
                continue

            count = int(retry_meta.get("count") or 0)
            history = retry_meta.get("history") or []
            if count <= 0 and not history:
                logger.info("跳过空重试数据的工单: incident=%s", incident.id)
                continue

            incident.retry_count = count
            incident.retry_history = history
            migrated += 1
            logger.info(
                "迁移重试数据: incident=%s, retry_count=%s, history_rounds=%s",
                incident.id,
                count,
                len(history),
            )

        await session.commit()
    logger.info("重试数据迁移完成: scanned=%s, migrated=%s", len(incidents), migrated)
    return migrated


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
    await session.refresh(log)
    return log


async def create_execution(session: AsyncSession, execution: Execution) -> Execution:
    """Create an execution row for one auto-executed action."""
    logger.info(
        "创建执行记录: incident_id=%s, status=%s, action=%s",
        execution.incident_id,
        execution.status,
        execution.action,
    )
    session.add(execution)
    await session.commit()
    await session.refresh(execution)
    logger.info("执行记录已创建: id=%s, incident_id=%s", execution.id, execution.incident_id)
    return execution


async def update_execution(session: AsyncSession, execution_id: int, **kwargs) -> Execution | None:
    """Update execution status/result during longer-running actions."""
    result = await session.execute(select(Execution).where(Execution.id == execution_id))
    execution = result.scalar_one_or_none()
    if not execution:
        logger.warning("执行记录不存在: id=%s", execution_id)
        return None
    for key, value in kwargs.items():
        setattr(execution, key, value)
    await session.commit()
    await session.refresh(execution)
    logger.info("执行记录已更新: id=%s, fields=%s", execution_id, list(kwargs.keys()))
    return execution


async def list_executions_by_incident(
    session: AsyncSession,
    incident_id: str,
    limit: int = 50,
) -> list[Execution]:
    """List execution rows for one incident, newest first."""
    result = await session.execute(
        select(Execution)
        .where(Execution.incident_id == incident_id)
        .order_by(Execution.created_at.desc())
        .limit(limit)
    )
    executions = list(result.scalars().all())
    logger.info(
        "查询工单执行记录: incident_id=%s, limit=%s, count=%s",
        incident_id,
        limit,
        len(executions),
    )
    return executions


async def list_executions(session: AsyncSession, limit: int = 100) -> list[Execution]:
    """List recent executions for the Web Console overview."""
    result = await session.execute(
        select(Execution).order_by(Execution.created_at.desc()).limit(limit)
    )
    executions = list(result.scalars().all())
    logger.info("查询最近执行记录: limit=%s, count=%s", limit, len(executions))
    return executions


async def create_report(session: AsyncSession, report: Report) -> Report:
    """Persist a generated incident report."""
    logger.info("创建故障报告: incident_id=%s", report.incident_id)
    session.add(report)
    await session.commit()
    await session.refresh(report)
    logger.info("故障报告已创建: id=%s, incident_id=%s", report.id, report.incident_id)
    return report


async def get_report_by_incident(session: AsyncSession, incident_id: str) -> Report | None:
    """Return the newest report for an incident."""
    result = await session.execute(
        select(Report)
        .where(Report.incident_id == incident_id)
        .order_by(Report.created_at.desc())
        .limit(1)
    )
    report = result.scalar_one_or_none()
    if report:
        logger.info("查询故障报告完成: incident_id=%s, report_id=%s", incident_id, report.id)
    else:
        logger.warning("故障报告不存在: incident_id=%s", incident_id)
    return report


async def list_reports(session: AsyncSession, limit: int = 50) -> list[Report]:
    """List recent generated reports."""
    result = await session.execute(
        select(Report).order_by(Report.created_at.desc()).limit(limit)
    )
    reports = list(result.scalars().all())
    logger.info("查询故障报告列表: limit=%s, count=%s", limit, len(reports))
    return reports
