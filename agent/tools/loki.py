# agent/tools/loki.py
import logging
from datetime import datetime, timedelta
import httpx

from agent.config import settings

logger = logging.getLogger("ops-agent.tools.loki")

LOKI_API = f"{settings.loki_url}/loki/api/v1"


async def query_service_logs(service: str, keyword: str = "ERROR", minutes: int = 10) -> list:
    """Query logs for a specific service"""
    query = f'{{app="{service}"}} |= "{keyword}"'

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{LOKI_API}/query_range",
            params={
                "query": query,
                "limit": 50,
                "start": int((datetime.utcnow() - timedelta(minutes=minutes)).timestamp() * 1e9),
                "end": int(datetime.utcnow().timestamp() * 1e9),
            },
        )
        data = resp.json()
        if data.get("status") != "success":
            logger.warning(f"Loki query failed: {query}")
            return []

        results = []
        for stream in data.get("data", {}).get("result", []):
            for ts, line in stream.get("values", []):
                results.append({"timestamp": ts, "line": line, "labels": stream.get("stream", {})})

        logger.info(f"Loki logs for {service}: {len(results)} entries (keyword={keyword})")
        return results


async def count_error_logs(service: str, minutes: int = 10) -> int:
    """Count error log entries"""
    logs = await query_service_logs(service, "ERROR", minutes)
    return len(logs)
