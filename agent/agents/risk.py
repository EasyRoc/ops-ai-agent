import logging

logger = logging.getLogger("ops-agent.risk")

# 风险评估只负责“给人看”和“给审批流判断”，不会执行任何处置命令。
# 这里的分数是一个简单、可解释的规则模型：先取 Runbook 步骤里的最高风险，
# 再根据生产环境、核心服务、告警级别等上下文做加权。
RISK_SCORE = {
    "低风险": 10,
    "中风险": 30,
    "高风险": 60,
    "极高风险": 90,
}

# Phase 2 只生成方案和审批状态，不真正执行命令。
# 白名单用于提前标记“这个方案如果未来要自动化执行，是否属于可控动作”。
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
    """对处置方案进行可解释的风险评估

    返回结构里的 `factors` 是”为什么是这个等级”，`warnings` 是”审批时需要注意什么”。
    调用方会把这两类信息展示在飞书诊断卡片和 Incident 详情中。
    """
    logger.info(
        "进入风险评估: service=%s, env=%s, severity=%s, steps=%s",
        service,
        env,
        alert_severity,
        len(steps or []),
    )

    if not steps:
        logger.info("风险评估: 没有处置步骤，返回低风险默认结果")
        return {
            "level": "低风险",
            "score": 0,
            "factors": ["无处置步骤"],
            "warnings": [],
            "allowed": True,
        }

    # Runbook 通常包含多个步骤，审批时先以最高风险步骤作为基础风险。
    max_risk_step = max(steps, key=lambda step: RISK_SCORE.get(step.risk_level, 0))
    score = RISK_SCORE.get(max_risk_step.risk_level, 0)
    factors = [f"最高风险步骤: [{max_risk_step.risk_level}] {max_risk_step.description}"]
    warnings = []
    logger.info(
        "风险评估基础分: risk_level=%s, score=%s, description=%s",
        max_risk_step.risk_level,
        score,
        max_risk_step.description,
    )

    # 生产环境里的高风险操作要明显提醒，后续可以扩展为“双人审批”策略。
    if env == "prod" and score >= RISK_SCORE["高风险"]:
        score = min(score + 15, 100)
        warnings.append("生产环境执行高风险操作，建议双人审批")
        logger.info("风险评估加权: 生产环境高风险操作 +15, score=%s", score)

    if alert_severity in {"P0", "P1"}:
        factors.append("P0/P1 告警，时效性优先")
        logger.info("风险评估记录因素: 高优先级告警 severity=%s", alert_severity)

    # 核心服务影响范围更大，即使动作本身是中风险，也要提高人工关注度。
    if service in CORE_SERVICES:
        score = min(score + 10, 100)
        factors.append(f"核心服务 {service}，影响范围较大")
        logger.info("风险评估加权: 核心服务 %s +10, score=%s", service, score)

    allowed = True
    for step in steps:
        if step.command and not any(step.command.startswith(action) for action in ALLOWED_ACTIONS):
            allowed = False
            warnings.append(f"动作不在白名单: {step.command}")
            logger.warning("风险评估发现非白名单命令: command=%s", step.command)

    # 分数到等级的映射保持简单，方便在飞书卡片和 Web Console 中解释。
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
