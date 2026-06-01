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
    logger.info(f"CMDB 查询: {service} -> 负责人={info.get('owner', '未知')}")
    return info


async def get_service_owner(service: str) -> str:
    info = await get_service_info(service)
    owner = info.get("owner", "unknown")
    if owner == "unknown":
        logger.warning(f"CMDB: 服务 '{service}' 未找到，负责人未知")
    return owner


async def get_service_dependencies(service: str) -> list:
    info = await get_service_info(service)
    deps = info.get("dependencies", [])
    if not deps:
        logger.warning(f"CMDB: 服务 '{service}' 无依赖记录")
    return deps


async def get_service_chat_id(service: str) -> str:
    # 优先从 .env 读取，没有则回退到 MOCK_CMDB
    from agent.config import settings
    chat_id = settings.service_chat_map.get(service, "")
    if chat_id:
        logger.info(f"CMDB: 服务 '{service}' chat_id 来自 .env 配置 ({chat_id})")
        return chat_id

    info = await get_service_info(service)
    chat_id = info.get("chat_id", "")
    if not chat_id:
        logger.warning(f"CMDB: 服务 '{service}' 未配置飞书群 chat_id（.env 和 CMDB 均无）")
    return chat_id
