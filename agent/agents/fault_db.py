import logging

from agent.db.crud import AsyncSessionLocal, list_reports

logger = logging.getLogger("ops-agent.fault_db")


def calculate_runbook_score(executions: list[dict]) -> dict:
    """根据历史执行结果计算 Runbook 成功率评分。"""
    logger.info("进入 calculate_runbook_score: executions=%s", len(executions or []))
    if not executions:
        return {"total": 0, "success": 0, "score": 0}

    total = len(executions)
    success = sum(1 for item in executions if item.get("status") == "success")
    score = round(success / total * 100, 2)
    result = {"total": total, "success": success, "score": score}
    logger.info("Runbook 历史评分完成: %s", result)
    return result


async def find_similar_incidents(service: str | None = None, alertname: str | None = None, limit: int = 20) -> list[dict]:
    """从 reports.fault_patterns 中筛选相似故障。

    这是一个轻量历史故障库，先用结构化标签做精确/半精确匹配；后续可以替换成向量检索。
    """
    logger.info("进入 find_similar_incidents: service=%s, alertname=%s, limit=%s", service, alertname, limit)
    async with AsyncSessionLocal() as session:
        reports = await list_reports(session, limit=limit)

    matches = []
    for report in reports:
        patterns = report.fault_patterns or {}
        if service and patterns.get("service") != service:
            continue
        if alertname and patterns.get("alertname") != alertname:
            continue
        matches.append(
            {
                "incident_id": report.incident_id,
                "created_at": report.created_at.isoformat() if report.created_at else None,
                "patterns": patterns,
            }
        )
    logger.info("相似故障查询完成: count=%s", len(matches))
    return matches


async def score_runbook(runbook_name: str, limit: int = 100) -> dict:
    """按历史报告中的验证结果估算 Runbook 有效率。

    当前不额外建表，因此使用 reports.fault_patterns 里的 `runbook` 和 `recovered`
    做统计。等 executions 表补充 runbook_name 后，可以把这里切到更精确的执行维度。
    """
    logger.info("进入 score_runbook: runbook=%s, limit=%s", runbook_name, limit)
    async with AsyncSessionLocal() as session:
        reports = await list_reports(session, limit=limit)

    executions = []
    for report in reports:
        patterns = report.fault_patterns or {}
        if patterns.get("runbook") != runbook_name:
            continue
        executions.append({"status": "success" if patterns.get("recovered") else "failed"})

    score = calculate_runbook_score(executions)
    logger.info("Runbook 有效率评分完成: runbook=%s, score=%s", runbook_name, score)
    return score
