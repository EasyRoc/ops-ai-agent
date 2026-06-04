import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.crud import get_report_by_incident, get_session, list_reports

logger = logging.getLogger("ops-agent.api.reports")
router = APIRouter(prefix="/api/v1")


def _serialize_report(report) -> dict:
    """把 ORM Report 转为前端友好的 JSON。"""
    return {
        "id": report.id,
        "incident_id": report.incident_id,
        "content": report.content,
        "fault_patterns": getattr(report, "fault_patterns", None),
        "created_at": report.created_at.isoformat() if report.created_at else None,
    }


@router.get("/reports/{incident_id}")
async def get_report_endpoint(
    incident_id: str,
    format: str = "json",
    db: AsyncSession = Depends(get_session),
):
    """返回单个 Incident 的报告，支持 ?format=json|markdown。"""
    logger.info("进入 get_report_endpoint: incident=%s, format=%s", incident_id, format)
    report = await get_report_by_incident(db, incident_id)
    if not report:
        logger.warning("报告查询失败，报告不存在: incident=%s", incident_id)
        raise HTTPException(status_code=404, detail="Report not found")

    if format == "markdown":
        logger.info("返回 Markdown 报告: incident=%s", incident_id)
        return Response(content=report.content, media_type="text/markdown; charset=utf-8")

    logger.info("返回 JSON 报告: incident=%s", incident_id)
    return _serialize_report(report)


@router.get("/reports")
async def list_reports_endpoint(
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
):
    """列出最近生成的故障报告。"""
    logger.info("进入 list_reports_endpoint: limit=%s", limit)
    reports = await list_reports(db, limit=limit)
    return {
        "total": len(reports),
        "reports": [_serialize_report(report) for report in reports],
    }
