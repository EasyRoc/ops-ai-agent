# agent/agents/supervisor.py
import logging
from agent.workflows.alert_workflow import AlertState, build_alert_workflow

logger = logging.getLogger("ops-agent.supervisor")


async def run_alert_workflow(alert_raw: dict) -> dict:
    """Run the full alert-handling workflow, return final diagnosis result"""
    workflow = build_alert_workflow()

    initial_state: AlertState = {
        "alert_raw": alert_raw,
        "incident_id": None,
        "alert_parsed": None,
        "context": None,
        "diagnosis": None,
        "error": None,
    }

    result = await workflow.ainvoke(initial_state)
    logger.info(f"Workflow completed for incident: {result.get('incident_id')}")
    return result


async def collect_context_for_incident(state: AlertState) -> AlertState:
    """Collect observability context data for the alert"""
    from agent.tools.prometheus import query_service_metrics
    from agent.tools.loki import query_service_logs
    from agent.tools.kubernetes import get_service_pods
    from agent.tools.cmdb import get_service_info

    service = state["alert_parsed"]["service"]
    env = state["alert_parsed"].get("env", "prod")

    try:
        metrics = await query_service_metrics(service)
        logs = await query_service_logs(service)
        pods = await get_service_pods(service, namespace=f"demo")
        cmdb_info = await get_service_info(service)

        state["context"] = {
            "metrics": metrics,
            "logs": logs,
            "pods": pods,
            "cmdb": cmdb_info,
        }
        logger.info(f"Context collected for {service}: {len(metrics)} metrics, {len(logs)} log entries")
    except Exception as e:
        logger.error(f"Context collection failed: {e}")
        state["error"] = str(e)

    return state
