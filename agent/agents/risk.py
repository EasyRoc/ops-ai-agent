import logging

logger = logging.getLogger("ops-agent.risk")

RISK_SCORE = {
    "低风险": 10,
    "中风险": 30,
    "高风险": 60,
    "极高风险": 90,
}

ALLOWED_ACTIONS = (
    "kubectl scale deployment",
    "kubectl delete pod",
    "kubectl rollout undo deployment",
    "kubectl set resources deployment",
    "kubectl get pods",
    "kubectl describe pod",
)

CORE_SERVICES = {"payment-service", "order-service"}


def evaluate_risk(steps: list, alert_severity: str, service: str, env: str) -> dict:
    if not steps:
        return {
            "level": "低风险",
            "score": 0,
            "factors": ["无处置步骤"],
            "warnings": [],
            "allowed": True,
        }

    max_risk_step = max(steps, key=lambda step: RISK_SCORE.get(step.risk_level, 0))
    score = RISK_SCORE.get(max_risk_step.risk_level, 0)
    factors = [f"最高风险步骤: [{max_risk_step.risk_level}] {max_risk_step.description}"]
    warnings = []

    if env == "prod" and score >= RISK_SCORE["高风险"]:
        score = min(score + 15, 100)
        warnings.append("生产环境执行高风险操作，建议双人审批")

    if alert_severity in {"P0", "P1"}:
        factors.append("P0/P1 告警，时效性优先")

    if service in CORE_SERVICES:
        score = min(score + 10, 100)
        factors.append(f"核心服务 {service}，影响范围较大")

    allowed = True
    for step in steps:
        if step.command and not any(step.command.startswith(action) for action in ALLOWED_ACTIONS):
            allowed = False
            warnings.append(f"动作不在白名单: {step.command}")

    if score >= 75:
        level = "极高风险"
    elif score >= 50:
        level = "高风险"
    elif score >= 25:
        level = "中风险"
    else:
        level = "低风险"

    logger.info(
        "风险评估: 服务=%s, 等级=%s, 分数=%s, 白名单=%s",
        service,
        level,
        score,
        "通过" if allowed else "拒绝",
    )
    return {
        "level": level,
        "score": score,
        "factors": factors,
        "warnings": warnings,
        "allowed": allowed,
    }
