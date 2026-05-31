# agent/agents/rca.py
import logging

from agent.db.crud import update_incident, AsyncSessionLocal
from agent.workflows.alert_workflow import AlertState

logger = logging.getLogger("ops-agent.rca")


async def analyze_root_cause(state: AlertState) -> AlertState:
    """Main RCA entry point called by the workflow.

    Extracts context and alert info from state, dispatches to the appropriate
    diagnosis function based on alert type, persists results to DB, and pushes
    a diagnosis card to Feishu.
    """
    context = state.get("context", {})
    alert = state.get("alert_parsed", {})
    incident_id = state.get("incident_id")

    if not context or not incident_id:
        logger.warning("RCA skipped: missing context or incident_id")
        return state

    alert_name = alert.get("alertname", "")

    try:
        if "CPU" in alert_name.upper():
            diagnosis = _diagnose_cpu_high(context)
        else:
            diagnosis = _diagnose_generic(context, alert)

        await _save_diagnosis(incident_id, diagnosis)
        await _notify_diagnosis(incident_id, diagnosis, alert)

        state["diagnosis"] = diagnosis
        logger.info(
            f"Diagnosis complete for {incident_id}: "
            f"root_cause={diagnosis['root_cause']}, "
            f"confidence={diagnosis['confidence']}"
        )
    except Exception as e:
        logger.error(f"RCA failed: {e}", exc_info=True)
        state["error"] = str(e)

    return state


def _diagnose_cpu_high(context: dict) -> dict:
    """CPU high diagnosis decision tree.

    Uses QPS and pod health signals to classify the root cause of a CPU spike.

    Decision rules:
      - QPS > 100 AND all pods healthy -> traffic-driven resource shortage
      - Not all pods healthy AND QPS < 50 -> single-instance anomaly / dead loop
      - Otherwise -> indeterminate, recommend human investigation
    """
    metrics = context.get("metrics", {})
    pods = context.get("pods", {})

    cpu = metrics.get("cpu", {})
    qps = metrics.get("qps", {})

    cpu_current = cpu.get("current", 0)
    qps_current = qps.get("current", 0)

    total_pods = pods.get("total", 0)
    ready_pods = pods.get("ready", 0)
    all_pods_healthy = ready_pods == total_pods and total_pods > 0

    evidence = [
        f"CPU使用率: {cpu_current:.1f}%",
        f"QPS: {qps_current:.1f} req/s",
        f"Pod状态: {ready_pods}/{total_pods} Ready",
    ]

    if qps_current > 100 and all_pods_healthy:
        root_cause = "流量上涨导致服务资源不足"
        confidence = 0.85
        evidence.append(
            f"所有{total_pods}个实例CPU均高，QPS({qps_current:.1f})显著上升，"
            "判断为流量驱动型资源不足"
        )
    elif not all_pods_healthy and qps_current < 50:
        root_cause = "单实例异常或代码死循环"
        confidence = 0.70
        not_ready_count = total_pods - ready_pods
        evidence.append(
            f"仅{not_ready_count}个实例异常(共{total_pods}个)，"
            f"QPS({qps_current:.1f})不高，判断为单实例故障或代码问题"
        )
    else:
        root_cause = "CPU异常升高，需进一步排查"
        confidence = 0.40
        evidence.append(
            f"当前数据不足以确定具体根因"
            f"(QPS={qps_current:.1f}, Pods={ready_pods}/{total_pods})，"
            "建议人工介入"
        )

    return {
        "root_cause": root_cause,
        "confidence": confidence,
        "evidence": evidence,
    }


def _diagnose_generic(context: dict, alert: dict) -> dict:
    """Generic fallback diagnosis for alert types without dedicated logic."""
    alert_name = alert.get("alertname", "未知")
    return {
        "root_cause": f"收到{alert_name}告警，等待扩展诊断能力",
        "confidence": 0.3,
        "evidence": [
            f"告警类型: {alert_name}",
            "该告警类型暂无专用诊断逻辑，建议人工确认",
        ],
    }


async def _save_diagnosis(incident_id: str, diagnosis: dict):
    """Persist root_cause, confidence, evidence and update status to DB."""
    async with AsyncSessionLocal() as session:
        await update_incident(
            session,
            incident_id,
            root_cause=diagnosis.get("root_cause"),
            confidence=diagnosis.get("confidence"),
            evidence=diagnosis.get("evidence"),
            status="diagnosed",
        )
        logger.info(f"Diagnosis saved to DB for {incident_id}")


async def _notify_diagnosis(incident_id: str, diagnosis: dict, alert: dict):
    """Push a diagnosis result card to the service's Feishu chat.

    Errors during notification are logged but do not propagate — the workflow
    continues regardless of delivery success.
    """
    from agent.channels.feishu import send_card_to_chat
    from agent.templates import render_card
    from agent.tools.cmdb import get_service_chat_id

    severity_color_map = {
        "P0": "red",
        "P1": "orange",
        "P2": "yellow",
        "P3": "blue",
    }

    try:
        service = alert.get("service", "unknown")
        severity = alert.get("severity", "P3")
        alert_name = alert.get("alertname", "未知")

        card = render_card(
            "diagnosis_card",
            alert_title=f"[{severity}] {service} - {alert_name}",
            severity_color=severity_color_map.get(severity, "blue"),
            root_cause=diagnosis.get("root_cause", ""),
            evidence_list="\n".join(diagnosis.get("evidence", [])),
            confidence=f"{diagnosis.get('confidence', 0) * 100:.0f}",
            incident_id=incident_id,
            status="待确认",
            duration="刚刚",
        )

        chat_id = await get_service_chat_id(service)
        if chat_id:
            result = await send_card_to_chat(chat_id, card)
            logger.info(
                f"Diagnosis notification sent to chat {chat_id} "
                f"for incident {incident_id}: code={result.get('code')}"
            )
        else:
            logger.warning(
                f"No chat_id found for service '{service}', "
                f"skipping Feishu notification for {incident_id}"
            )
    except Exception as e:
        logger.error(
            f"Failed to send diagnosis notification for {incident_id}: {e}"
        )
