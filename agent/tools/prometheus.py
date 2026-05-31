# agent/tools/prometheus.py
import logging
from datetime import datetime, timedelta
import httpx

from agent.config import settings

logger = logging.getLogger("ops-agent.tools.prometheus")

PROM_API = f"{settings.prometheus_url}/api/v1"


async def _query(promql: str) -> list:
    """Execute a PromQL instant query"""
    logger.debug(f"PromQL 查询: {promql[:120]}...")
    async with httpx.AsyncClient(trust_env=False) as client:
        resp = await client.get(f"{PROM_API}/query", params={"query": promql})
        data = resp.json()
        if data["status"] != "success":
            logger.warning(f"PromQL 查询失败: {promql[:100]} -> {data.get('error')}")
            return []
        results = data["data"]["result"]
        logger.debug(f"PromQL 返回 {len(results)} 条时序")
        return results


async def query_service_metrics(service: str) -> dict:
    """Query core metrics for a service"""
    queries = {
        "cpu": f'max(process_cpu_usage{{service="{service}"}}) * 100',
        "memory": f'jvm_memory_used_bytes{{service="{service}"}}',
        "qps": f'rate(http_server_requests_seconds_count{{service="{service}"}}[5m])',
        "rt_avg": f'rate(http_server_requests_seconds_sum{{service="{service}"}}[5m]) / rate(http_server_requests_seconds_count{{service="{service}"}}[5m])',
        "error_rate": f'rate(http_server_requests_seconds_count{{service="{service}",status=~"5.."}}[5m]) / rate(http_server_requests_seconds_count{{service="{service}"}}[5m])',
    }

    results = {}
    for name, promql in queries.items():
        try:
            data = await _query(promql)
            results[name] = {
                "current": float(data[0]["value"][1]) if data else 0,
                "samples": len(data),
            }
        except Exception as e:
            logger.error(f"指标查询失败 [{name}]: {e}")
            results[name] = {"current": 0, "error": str(e)}

    logger.info(f"服务指标 {service}: CPU={results.get('cpu',{}).get('current',0):.1f}%, QPS={results.get('qps',{}).get('current',0):.1f}")
    return results
