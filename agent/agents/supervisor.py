# agent/agents/supervisor.py
import logging
from agent.workflows.alert_workflow import AlertState, build_alert_workflow

logger = logging.getLogger("ops-agent.supervisor")


async def run_alert_workflow(alert_raw: dict) -> dict:
    """Run the full alert-handling workflow and return the final state.

    这个入口主要用于测试或内部直接调用；线上 Webhook 路径在 `api/v1/alerts.py`
    中构造同样的初始状态。
    """
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
        "runbook": None,
        "risk_assessment": None,
        "approval_status": None,
        "error": None,
    }

    result = await workflow.ainvoke(initial_state)
    logger.info(f"工作流执行完成: 工单={result.get('incident_id')}, 错误={result.get('error')}")
    return result


async def collect_context_for_incident(state: AlertState) -> AlertState:
    """Collect metrics, logs, Kubernetes state and service metadata for RCA."""
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
        logger.info("上下文采集: 查询 Prometheus 指标开始 [%s]", service)
        metrics = await query_service_metrics(service)
        logger.info("上下文采集: Prometheus 指标完成 [%s], keys=%s", service, list(metrics.keys()))
    except Exception as e:
        logger.error(f"Prometheus 查询失败 [{service}]: {e}")
        errors.append(f"prometheus: {e}")

    try:
        logger.info("上下文采集: 查询 Loki 日志开始 [%s]", service)
        logs = await query_service_logs(service)
        logger.info("上下文采集: Loki 日志完成 [%s], lines=%s", service, len(logs))
    except Exception as e:
        logger.error(f"Loki 查询失败 [{service}]: {e}")
        errors.append(f"loki: {e}")

    try:
        logger.info("上下文采集: 查询 Kubernetes Pod 开始 [%s]", service)
        pods = await get_service_pods(service, namespace="demo")
        logger.info(
            "上下文采集: Kubernetes Pod 完成 [%s], ready=%s/%s",
            service,
            pods.get("ready", 0),
            pods.get("total", 0),
        )
    except Exception as e:
        logger.error(f"K8s 查询失败 [{service}]: {e}")
        errors.append(f"k8s: {e}")

    try:
        logger.info("上下文采集: 查询 CMDB 开始 [%s]", service)
        cmdb_info = await get_service_info(service)
        logger.info(
            "上下文采集: CMDB 完成 [%s], owner=%s, team=%s",
            service,
            cmdb_info.get("owner", "-"),
            cmdb_info.get("team", "-"),
        )
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
