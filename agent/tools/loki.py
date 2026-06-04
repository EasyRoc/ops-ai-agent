# agent/tools/loki.py
import logging
from datetime import datetime, timedelta, timezone
import httpx

from agent.config import settings

logger = logging.getLogger("ops-agent.tools.loki")

LOKI_API = f"{settings.loki_url}/loki/api/v1"


async def query_service_logs(service: str, keyword: str = "ERROR", minutes: int = 10) -> list:
    """Query logs for a specific service"""
    query = f'{{app="{service}"}} |= "{keyword}"'
    logger.info(f"Loki 日志查询: 服务={service}, 关键词={keyword}, 时间窗口={minutes}分钟")

    now = datetime.now(timezone.utc)

    async with httpx.AsyncClient(trust_env=False) as client:
        resp = await client.get(
            f"{LOKI_API}/query_range",
            params={
                "query": query,
                "limit": 50,
                "start": int((now - timedelta(minutes=minutes)).timestamp() * 1e9),
                "end": int(now.timestamp() * 1e9),
            },
        )
        data = resp.json()
        if data.get("status") != "success":
            logger.warning(f"Loki 查询失败: 状态={data.get('status')}, 查询={query}")
            return []

        results = []
        for stream in data.get("data", {}).get("result", []):
            for ts, line in stream.get("values", []):
                results.append({"timestamp": ts, "line": line, "labels": stream.get("stream", {})})

        logger.info(f"Loki 日志结果 {service}: {len(results)} 条 (关键词={keyword})")
        return results


async def count_error_logs(service: str, minutes: int = 10) -> int:
    """Count error log entries"""
    count = len(await query_service_logs(service, "ERROR", minutes))
    logger.info(f"Loki 错误日志计数 {service}: {count} 条 (窗口={minutes}分钟)")
    return count
