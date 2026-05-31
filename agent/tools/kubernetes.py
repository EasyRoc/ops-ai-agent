# agent/tools/kubernetes.py
import logging
import httpx

logger = logging.getLogger("ops-agent.tools.k8s")

K8S_API = "http://localhost:8001"


async def get_service_pods(service: str, namespace: str = "demo") -> dict:
    """Get pod status for a service"""
    logger.info(f"K8s 查询Pod: 服务={service}, 命名空间={namespace}")
    async with httpx.AsyncClient(trust_env=False) as client:
        resp = await client.get(
            f"{K8S_API}/api/v1/namespaces/{namespace}/pods",
            params={"labelSelector": f"app={service}"},
        )
        if resp.status_code != 200:
            logger.error(f"K8s 查询Pod失败: 服务={service}, HTTP状态={resp.status_code}")
            return {"error": f"k8s api error: {resp.status_code}"}

        data = resp.json()
        pods = []
        for item in data.get("items", []):
            status = item.get("status", {})
            container_statuses = status.get("containerStatuses", [])
            pods.append({
                "name": item["metadata"]["name"],
                "phase": status.get("phase"),
                "ready": all(cs.get("ready", False) for cs in container_statuses),
                "restarts": sum(cs.get("restartCount", 0) for cs in container_statuses),
                "node": item["spec"].get("nodeName"),
            })

        result = {
            "total": len(pods),
            "ready": sum(1 for p in pods if p["ready"]),
            "pods": pods,
        }
        logger.info(f"K8s 查询Pod完成: 服务={service}, 总数={result['total']}, 就绪={result['ready']}")
        return result


async def get_pod_events(service: str, namespace: str = "demo") -> list:
    """Get K8S events for a service"""
    logger.info(f"K8s 查询事件: 服务={service}, 命名空间={namespace}")
    async with httpx.AsyncClient(trust_env=False) as client:
        resp = await client.get(
            f"{K8S_API}/api/v1/namespaces/{namespace}/events",
            params={"fieldSelector": f"involvedObject.name~={service}"},
        )
        if resp.status_code != 200:
            logger.error(f"K8s 查询事件失败: 服务={service}, HTTP状态={resp.status_code}")
            return []

        data = resp.json()
        events = []
        for item in data.get("items", []):
            events.append({
                "type": item.get("type"),
                "reason": item.get("reason"),
                "message": item.get("message"),
                "timestamp": item.get("lastTimestamp") or item["metadata"]["creationTimestamp"],
            })
        logger.info(f"K8s 查询事件完成: 服务={service}, 事件数={len(events)}")
        return events
