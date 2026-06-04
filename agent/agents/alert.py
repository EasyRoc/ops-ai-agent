# agent/agents/alert.py
import logging
import redis.asyncio as aioredis

from agent.config import settings
from agent.db.models import Incident
from agent.db.crud import create_incident, AsyncSessionLocal
from agent.workflows.alert_workflow import AlertState

logger = logging.getLogger("ops-agent.alert")


async def _get_redis():
    """Create a short-lived Redis client for alert deduplication."""
    return aioredis.from_url(f"redis://{settings.redis_host}:{settings.redis_port}")


async def parse_and_create_incident(state: AlertState) -> AlertState:
    """Parse a normalized alert payload, deduplicate it, and create an Incident."""
    alert = state["alert_raw"]
    redis = await _get_redis()
    logger.info(
        "进入 parse_and_create_incident: alert=%s, service=%s, severity=%s, fingerprint=%s",
        alert.get("alertname"),
        alert.get("service"),
        alert.get("severity"),
        alert.get("fingerprint", "?")[:12],
    )

    try:
        fingerprint = alert.get("fingerprint", "")
        dedup_key = f"alert:dedup:{fingerprint}"
        logger.info("告警去重检查: dedup_key=%s, window=%ss", dedup_key, settings.alert_dedup_window)

        # 去重命中时直接复用原 Incident，避免同一 firing 告警重复触发诊断和飞书通知。
        existing = await redis.get(dedup_key)
        if existing:
            existing_id = existing.decode()
            logger.info(f"重复告警已跳过: {alert.get('alertname')} (指纹={fingerprint}, 工单={existing_id})")
            state["incident_id"] = existing_id
            state["alert_parsed"] = alert
            return state

        # 先写空占位可以降低并发 webhook 同时创建多个 Incident 的概率。
        logger.debug(f"设置去重占位: 指纹={fingerprint}")
        await redis.setex(dedup_key, settings.alert_dedup_window, "")

        # Incident 是后续上下文、诊断、Runbook、审批状态的主线索引。
        async with AsyncSessionLocal() as session:
            incident = Incident(
                service=alert.get("service", "unknown"),
                env=alert.get("env", "prod"),
                severity=alert.get("severity", "P3"),
                alert_name=alert.get("alertname"),
                alert_value=alert.get("value"),
                status="diagnosing",
            )
            logger.info(f"创建工单: 服务={incident.service}, 告警={incident.alert_name}")
            incident = await create_incident(session, incident)

            # Incident 创建成功后，把去重占位替换成真实 incident_id，方便重复告警直接回填。
            await redis.setex(dedup_key, settings.alert_dedup_window, incident.id)
            logger.info(f"去重缓存已更新: 指纹={fingerprint} -> 工单={incident.id}")

            state["incident_id"] = incident.id
            state["alert_parsed"] = alert
            logger.info(f"工单已创建: {incident.id} 服务={incident.service}")

            # 飞书通知是 best-effort：失败会记录日志，但不影响 Incident 主流程。
            await _notify_feishu(incident, alert)

    except Exception as e:
        logger.error(f"告警解析失败: {e}", exc_info=True)
        state["error"] = str(e)
    finally:
        await redis.aclose()

    return state


async def _notify_feishu(incident: Incident, alert: dict):
    """Push the initial alert notification card to Feishu."""
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
        logger.info(
            "准备渲染告警卡片: incident=%s, service=%s, severity=%s",
            incident.id,
            incident.service,
            incident.severity,
        )
        card = render_card(
            "alert_card",
            alert_title=f"[{incident.severity}] {incident.service} - {incident.alert_name}",
            severity_color=severity_color_map.get(incident.severity, "blue"),
            service=incident.service,
            env=incident.env,
            severity=incident.severity,
            value=incident.alert_value or "",
            incident_id=incident.id,
        )
        chat_id = await get_service_chat_id(incident.service)
        if chat_id:
            logger.info(f"发送告警卡片到群 {chat_id}: 工单={incident.id}")
            result = await send_card_to_chat(chat_id, card)
            logger.info(f"飞书告警通知已发送: message_id={result.get('data', {}).get('message_id', '?')}")
        else:
            logger.warning(f"服务 '{incident.service}' 无 chat_id，跳过飞书告警通知")
    except Exception as e:
        logger.error(f"飞书告警通知失败: 工单={incident.id}, 错误={e}")
