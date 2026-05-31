# agent/agents/supervisor.py
import logging
from agent.workflows.alert_workflow import AlertState, build_alert_workflow

logger = logging.getLogger("ops-agent.supervisor")


async def run_alert_workflow(alert_raw: dict) -> dict:
    """Run the full alert-handling workflow, return final diagnosis result"""
    alert_name = alert_raw.get("alertname", "unknown")
    service = alert_raw.get("service", "unknown")
    logger.info(f"启动告警工作流: 告警={alert_name}, 服务={service}")

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
    logger.info(f"工作流执行完成: 工单={result.get('incident_id')}, 错误={result.get('error')}")
    return result


async def collect_context_for_incident(state: AlertState) -> AlertState:
    """Collect observability context data for the alert"""
    from agent.tools.prometheus import query_service_metrics
    from agent.tools.loki import query_service_logs
    from agent.tools.kubernetes import get_service_pods
    from agent.tools.cmdb import get_service_info

    service = state["alert_parsed"]["service"]
    env = state["alert_parsed"].get("env", "prod")

    logger.info(f"采集上下文: 服务={service}, 环境={env}")

    metrics, logs, pods, cmdb_info = {}, [], {}, {}
    errors = []

    try:
        metrics = await query_service_metrics(service)
    except Exception as e:
        logger.error(f"Prometheus 查询失败 [{service}]: {e}")
        errors.append(f"prometheus: {e}")

    try:
        logs = await query_service_logs(service)
    except Exception as e:
        logger.error(f"Loki 查询失败 [{service}]: {e}")
        errors.append(f"loki: {e}")

    try:
        pods = await get_service_pods(service, namespace="demo")
    except Exception as e:
        logger.error(f"K8s 查询失败 [{service}]: {e}")
        errors.append(f"k8s: {e}")

    try:
        cmdb_info = await get_service_info(service)
    except Exception as e:
        logger.error(f"CMDB 查询失败 [{service}]: {e}")
        errors.append(f"cmdb: {e}")

    state["context"] = {
        "metrics": metrics,
        "logs": logs,
        "pods": pods,
        "cmdb": cmdb_info,
    }

    if errors:
        logger.warning(f"上下文采集部分失败: {len(errors)} 个工具出错 [{service}]: {errors}")
    logger.info(f"上下文采集完成 [{service}]: 指标={len(metrics)} 项, 日志={len(logs)} 条, Pod={pods.get('total', 0)} 个")
    return state
