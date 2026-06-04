import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.crud import get_incident, get_session, list_executions, list_executions_by_incident

logger = logging.getLogger("ops-agent.api.executions")
router = APIRouter(prefix="/api/v1")


def _serialize_execution(execution) -> dict:
    """把 ORM Execution 转成 API 响应。"""
    return {
        "id": execution.id,
        "incident_id": execution.incident_id,
        "action": execution.action,
        "operator": execution.operator,
        "status": execution.status,
        "result": execution.result,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
    }


@router.get("/incidents/{incident_id}/executions")
async def list_incident_executions_endpoint(
    incident_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
):
    """查询某个 Incident 的执行记录。"""
    logger.info(
        "进入 list_incident_executions_endpoint: incident=%s, limit=%s",
        incident_id,
        limit,
    )
    executions = await list_executions_by_incident(db, incident_id, limit=limit)
    return {
        "incident_id": incident_id,
        "total": len(executions),
        "executions": [_serialize_execution(item) for item in executions],
    }


@router.get("/executions")
async def list_executions_endpoint(
    limit: int = 100,
    db: AsyncSession = Depends(get_session),
):
    """查询最近自动执行记录。"""
    logger.info("进入 list_executions_endpoint: limit=%s", limit)
    executions = await list_executions(db, limit=limit)
    return {
        "total": len(executions),
        "executions": [_serialize_execution(item) for item in executions],
    }


@router.post("/incidents/{incident_id}/execute")
async def execute_incident_endpoint(
    incident_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
):
    """手动触发某个已审批 Incident 的 Phase 3 执行工作流。"""
    logger.info("进入 execute_incident_endpoint: incident=%s", incident_id)
    incident = await get_incident(db, incident_id)
    if not incident:
        logger.warning("执行触发失败，工单不存在: incident=%s", incident_id)
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.approval_status != "approved":
        logger.warning(
            "执行触发失败，工单尚未批准: incident=%s, approval_status=%s",
            incident_id,
            incident.approval_status,
        )
        raise HTTPException(status_code=409, detail="Incident is not approved")

    from agent.api.v1.approvals import run_execution_workflow

    background_tasks.add_task(run_execution_workflow, incident_id, {"operator": {"name": "api"}})
    logger.info("执行工作流后台任务已入队: incident=%s", incident_id)
    return {"status": "accepted", "incident_id": incident_id}
