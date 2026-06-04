import asyncio
import logging
import math
import time

from agent.agents.audit import write_audit
from agent.db.crud import AsyncSessionLocal, update_incident
from agent.tools.prometheus import query_service_metrics

logger = logging.getLogger("ops-agent.verify")

# 告警类型到恢复指标的映射。阈值保持保守、可解释，便于在报告中复盘。
THRESHOLDS = {
    "CPU": {"metric": "cpu", "max": 70.0, "unit": "%"},
    "OOM": {"metric": "memory", "max": 0.85, "unit": "ratio"},
    "MEMORY": {"metric": "memory", "max": 0.85, "unit": "ratio"},
    "ERROR": {"metric": "error_rate", "max": 0.02, "unit": "ratio"},
    "LATENCY": {"metric": "rt_avg", "max": 1.0, "unit": "s"},
    "RT": {"metric": "rt_avg", "max": 1.0, "unit": "s"},
    "P99": {"metric": "rt_avg", "max": 1.0, "unit": "s"},
}


def _match_threshold(alert_name: str) -> dict:
    """根据告警名选择验证指标，默认按 CPU 告警处理。"""
    normalized = (alert_name or "").upper()
    for keyword, threshold in THRESHOLDS.items():
        if keyword in normalized:
            logger.info(
                "验证指标匹配完成: alert=%s, keyword=%s, metric=%s, max=%s",
                alert_name,
                keyword,
                threshold["metric"],
                threshold["max"],
            )
            return threshold
    logger.info("未命中特定验证指标，默认使用 CPU: alert=%s", alert_name)
    return THRESHOLDS["CPU"]


async def verify_recovery(
    incident_id: str,
    context: dict,
    alert_name: str,
    max_wait: int = 300,
    interval: int = 15,
) -> dict:
    """轮询 Prometheus 指标，判断执行后是否恢复。

    `max_wait` 是总等待窗口，`interval` 是轮询间隔。本地单测会传 interval=0，
    所以这里至少执行一次查询，避免测试和演示场景被 sleep 拖慢。
    """
    service = (context or {}).get("service") or "unknown"
    threshold = _match_threshold(alert_name)
    metric_name = threshold["metric"]
    max_value = float(threshold["max"])
    started = time.monotonic()
    attempts = max(1, math.ceil(max_wait / interval)) if interval > 0 else 1
    logger.info(
        "进入 verify_recovery: incident=%s, service=%s, alert=%s, metric=%s, max=%s, attempts=%s",
        incident_id,
        service,
        alert_name,
        metric_name,
        max_value,
        attempts,
    )

    last_value = None
    last_metrics = {}
    for attempt in range(1, attempts + 1):
        logger.info(
            "开始恢复验证轮询: incident=%s, attempt=%s/%s, service=%s",
            incident_id,
            attempt,
            attempts,
            service,
        )
        try:
            last_metrics = await query_service_metrics(service)
            metric = last_metrics.get(metric_name, {})
            last_value = float(metric.get("current", 0))
        except Exception as exc:
            logger.error(
                "恢复验证查询指标失败: incident=%s, service=%s, metric=%s, error=%s",
                incident_id,
                service,
                metric_name,
                exc,
                exc_info=True,
            )
            last_value = None

        if last_value is not None and last_value < max_value:
            duration = round(time.monotonic() - started, 3)
            logger.info(
                "恢复验证通过: incident=%s, metric=%s, value=%s, threshold=%s, duration=%.3fs",
                incident_id,
                metric_name,
                last_value,
                max_value,
                duration,
            )
            return {
                "recovered": True,
                "status": "recovered",
                "metric": metric_name,
                "current": last_value,
                "threshold": max_value,
                "attempts": attempt,
                "duration": duration,
                "metrics": last_metrics,
            }

        logger.info(
            "恢复验证未通过: incident=%s, metric=%s, current=%s, threshold=%s",
            incident_id,
            metric_name,
            last_value,
            max_value,
        )
        if interval > 0 and attempt < attempts:
            await asyncio.sleep(interval)

    duration = round(time.monotonic() - started, 3)
    logger.warning(
        "恢复验证超时: incident=%s, metric=%s, last_value=%s, threshold=%s, duration=%.3fs",
        incident_id,
        metric_name,
        last_value,
        max_value,
        duration,
    )
    return {
        "recovered": False,
        "status": "timeout",
        "metric": metric_name,
        "current": last_value,
        "threshold": max_value,
        "attempts": attempts,
        "duration": duration,
        "metrics": last_metrics,
        "reason": "指标未在验证窗口内恢复",
    }


async def update_incident_status(incident_id: str, status: str, **extra_fields) -> None:
    """验证节点的状态同步封装。"""
    logger.info(
        "进入 verify.update_incident_status: incident=%s, status=%s, extra=%s",
        incident_id,
        status,
        list(extra_fields.keys()),
    )
    try:
        async with AsyncSessionLocal() as session:
            await update_incident(session, incident_id, status=status, **extra_fields)
        logger.info("验证链路状态已更新: incident=%s, status=%s", incident_id, status)
    except Exception as exc:
        logger.error(
            "验证链路状态更新失败: incident=%s, status=%s, error=%s",
            incident_id,
            status,
            exc,
            exc_info=True,
        )


async def verify(state: dict) -> dict:
    """LangGraph 节点入口：执行后确认服务是否恢复。"""
    incident_id = state.get("incident_id") or ""
    alert = state.get("alert_parsed") or {}
    context = state.get("context") or {}
    alert_name = alert.get("alertname", "")
    logger.info(
        "进入 verify 节点: incident=%s, alert=%s, service=%s",
        incident_id,
        alert_name,
        context.get("service") or alert.get("service", "-"),
    )
    result = await verify_recovery(
        incident_id,
        {**context, "service": context.get("service") or alert.get("service", "unknown")},
        alert_name,
    )
    state["verification_result"] = result
    if result.get("recovered"):
        await update_incident_status(incident_id, "verified")
        await write_audit(incident_id, "system", "recovery_verified", result)
        logger.info("验证节点完成: incident=%s, recovered=True", incident_id)
    else:
        state["approval_status"] = "escalated"
        await update_incident_status(
            incident_id,
            "escalated",
            approval_status="escalated",
        )
        await write_audit(incident_id, "system", "recovery_verify_failed", result)
        logger.warning("验证节点未恢复，已升级人工: incident=%s", incident_id)
    return state
