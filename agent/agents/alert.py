# agent/agents/alert.py
import logging
import redis.asyncio as aioredis

from agent.config import settings
from agent.db.models import Incident
from agent.db.crud import create_incident, AsyncSessionLocal
from agent.workflows.alert_workflow import AlertState

logger = logging.getLogger("ops-agent.alert")


async def _get_redis():
    return aioredis.from_url(f"redis://{settings.redis_host}:{settings.redis_port}")


async def parse_and_create_incident(state: AlertState) -> AlertState:
    """Parse alert, deduplicate, and create Incident"""
    alert = state["alert_raw"]
    redis = await _get_redis()

    try:
        fingerprint = alert.get("fingerprint", "")
        dedup_key = f"alert:dedup:{fingerprint}"

        # Check if already processed within dedup window
        existing = await redis.get(dedup_key)
        if existing:
            logger.info(f"Duplicate alert skipped: {alert.get('alertname')} (fingerprint={fingerprint})")
            state["incident_id"] = existing.decode()
            state["alert_parsed"] = alert
            return state

        # Set dedup cache entry (expires after dedup window)
        await redis.setex(dedup_key, settings.alert_dedup_window, "")

        # Create Incident in database
        async with AsyncSessionLocal() as session:
            incident = Incident(
                service=alert.get("service", "unknown"),
                env=alert.get("env", "prod"),
                severity=alert.get("severity", "P3"),
                alert_name=alert.get("alertname"),
                alert_value=alert.get("value"),
                status="diagnosing",
            )
            incident = await create_incident(session, incident)

            # Update dedup cache with actual incident_id
            await redis.setex(dedup_key, settings.alert_dedup_window, incident.id)

            state["incident_id"] = incident.id
            state["alert_parsed"] = alert
            logger.info(f"Incident created: {incident.id} for {incident.service}")

            # Push to Feishu
            await _notify_feishu(incident, alert)

    except Exception as e:
        logger.error(f"Failed to parse alert: {e}", exc_info=True)
        state["error"] = str(e)
    finally:
        await redis.aclose()

    return state


async def _notify_feishu(incident: Incident, alert: dict):
    """Push alert notification card to Feishu"""
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
            result = await send_card_to_chat(chat_id, card)
            logger.info(f"Feishu notification sent: {result}")
    except Exception as e:
        logger.error(f"Feishu notification failed: {e}")
