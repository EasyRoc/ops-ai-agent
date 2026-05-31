import logging
from fastapi import APIRouter, Request, BackgroundTasks

from agent.workflows.alert_workflow import AlertState, build_alert_workflow

logger = logging.getLogger("ops-agent.api.alerts")
router = APIRouter(prefix="/api/v1")


@router.post("/alerts")
async def receive_alert(request: Request, background_tasks: BackgroundTasks):
    """Receive Alertmanager Webhook callback"""
    body = await request.json()
    logger.info(f"Received alert webhook: {body.get('receiver', 'unknown')}")

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
    workflow = build_alert_workflow()
    state: AlertState = {
        "alert_raw": alert_data,
        "incident_id": None,
        "alert_parsed": None,
        "context": None,
        "diagnosis": None,
        "error": None,
    }
    try:
        result = await workflow.ainvoke(state)
        logger.info(f"Diagnosis completed for {alert_data.get('service')}: incident={result.get('incident_id')}")
    except Exception as e:
        logger.error(f"Diagnosis failed: {e}", exc_info=True)
