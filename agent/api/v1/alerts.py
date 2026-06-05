import logging
from fastapi import APIRouter, Request, BackgroundTasks

from agent.workflows.alert_workflow import AlertState, build_alert_workflow

logger = logging.getLogger("ops-agent.api.alerts")
router = APIRouter(prefix="/api/v1")


@router.post("/alerts")
async def receive_alert(request: Request, background_tasks: BackgroundTasks):
    """Receive Alertmanager Webhook callback"""
    body = await request.json()
    logger.info(f"收到告警 Webhook: receiver={body.get('receiver', 'unknown')}")

    alerts = body.get("alerts", [])
    if not alerts:
        return {"status": "no_alerts"}

    results = []
    for alert in alerts:
        alert_data = {
            "alertname": alert.get("labels", {}).get("alertname", "unknown"),
            "service": alert.get("labels", {}).get("service", alert.get("annotations", {}).get("service", "unknown")),
            "env": alert.get("labels", {}).get("env", "prod"),
            "severity": alert.get("labels", {}).get("severity", "P3"),
            "value": alert.get("annotations", {}).get("value", alert.get("annotations", {}).get("summary", "")),
            "starts_at": alert.get("startsAt", ""),
            "fingerprint": alert.get("fingerprint", ""),
        }
        background_tasks.add_task(run_diagnosis, alert_data)
        results.append({"alertname": alert_data["alertname"], "status": "accepted"})

    return {"status": "ok", "alerts": len(results), "results": results}


async def run_diagnosis(alert_data: dict):
    """Run diagnosis in background"""
    logger.info(
        f"启动后台诊断: 告警={alert_data.get('alertname')}, "
        f"服务={alert_data.get('service')}, 指纹={alert_data.get('fingerprint', '?')[:12]}"
    )
    workflow = build_alert_workflow()
    state: AlertState = {
        "alert_raw": alert_data,
        "incident_id": None,
        "alert_parsed": None,
        "duplicate_alert": None,
        "context": None,
        "diagnosis": None,
        "runbook": None,
        "risk_assessment": None,
        "approval_status": None,
        "execution_result": None,
        "verification_result": None,
        "report": None,
        "operator": None,
        "error": None,
    }
    try:
        result = await workflow.ainvoke(state)
        logger.info(f"诊断完成: 服务={alert_data.get('service')}, incident={result.get('incident_id')}")
    except Exception as e:
        logger.error(f"诊断失败: 服务={alert_data.get('service')}, 错误={e}", exc_info=True)
