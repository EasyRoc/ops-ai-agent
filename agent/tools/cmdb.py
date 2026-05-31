# agent/tools/cmdb.py
import logging

logger = logging.getLogger("ops-agent.tools.cmdb")

MOCK_CMDB = {
    "frontend-service": {
        "owner": "张三",
        "team": "前端组",
        "dependencies": ["order-service"],
        "oncall": "张三",
        "chat_id": "oc_chat_frontend",
    },
    "order-service": {
        "owner": "李四",
        "team": "订单组",
        "dependencies": ["payment-service", "inventory-service", "redis", "postgres"],
        "oncall": "李四",
        "chat_id": "oc_chat_order",
    },
    "payment-service": {
        "owner": "王五",
        "team": "支付组",
        "dependencies": ["redis", "postgres"],
        "oncall": "王五",
        "chat_id": "oc_chat_payment",
    },
    "inventory-service": {
        "owner": "赵六",
        "team": "库存组",
        "dependencies": ["redis", "postgres"],
        "oncall": "赵六",
        "chat_id": "oc_chat_inventory",
    },
}


async def get_service_info(service: str) -> dict:
    """Get CMDB info for a service"""
    info = MOCK_CMDB.get(service, {})
    logger.info(f"CMDB lookup: {service} -> owner={info.get('owner', 'unknown')}")
    return info


async def get_service_owner(service: str) -> str:
    info = await get_service_info(service)
    return info.get("owner", "unknown")


async def get_service_dependencies(service: str) -> list:
    info = await get_service_info(service)
    return info.get("dependencies", [])


async def get_service_chat_id(service: str) -> str:
    info = await get_service_info(service)
    return info.get("chat_id", "")
