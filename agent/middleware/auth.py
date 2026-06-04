import logging

from fastapi.responses import JSONResponse

logger = logging.getLogger("ops-agent.middleware.auth")

# 这个项目当前是本地演示/开发环境，先用 Header 角色做最小 RBAC。
# 后续接入真实登录态时，只需要把角色提取逻辑替换掉，保护规则可以继续复用。
ROLE_PERMISSIONS = {
    "viewer": {"read"},
    "operator": {"read", "execute", "approve"},
    "admin": {"read", "execute", "approve", "admin"},
}

PUBLIC_PATHS = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/alerts",
    "/api/v1/approvals/callback",
)


def _required_permission(method: str, path: str) -> str:
    """根据请求方法和路径推导需要的权限。"""
    if method == "GET":
        return "read"
    if "/execute" in path:
        return "execute"
    if "/approval" in path or "/approvals" in path:
        return "approve"
    return "admin"


async def rbac_middleware(request, call_next):
    """RBAC 中间件入口。

    保护重点是“会改变系统状态”的执行入口。静态页面、健康检查、告警 Webhook、
    飞书卡片回调保持开放，避免本地部署时配置过重。
    """
    path = request.url.path
    method = request.method.upper()
    logger.info("进入 rbac_middleware: method=%s, path=%s", method, path)

    if method == "OPTIONS" or path == "/" or path.startswith(PUBLIC_PATHS):
        logger.info("RBAC 放行公共路径: method=%s, path=%s", method, path)
        return await call_next(request)

    required = _required_permission(method, path)
    role = (request.headers.get("x-user-role") or "viewer").lower()
    permissions = ROLE_PERMISSIONS.get(role, ROLE_PERMISSIONS["viewer"])
    logger.info(
        "RBAC 权限判断: role=%s, required=%s, permissions=%s",
        role,
        required,
        sorted(permissions),
    )

    if required not in permissions:
        logger.warning(
            "RBAC 拒绝请求: role=%s, required=%s, method=%s, path=%s",
            role,
            required,
            method,
            path,
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "Forbidden", "required": required, "role": role},
        )

    logger.info("RBAC 放行请求: role=%s, method=%s, path=%s", role, method, path)
    return await call_next(request)
