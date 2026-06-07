import logging
from datetime import datetime, timezone

from langgraph.graph import END, StateGraph

from agent.agents.audit import write_audit
from agent.agents.executor import (
    execute_kubectl,
    record_execution,
    select_executable_steps,
    update_incident_status,
)
from agent.agents.fallback import analyze_failure_and_retry
from agent.agents.rca import _format_action_plan
from agent.agents.supervisor import collect_context_for_incident
from agent.agents.verify import _resolve_verification, verify_recovery
from agent.db.crud import AsyncSessionLocal, update_incident
from agent.workflows.alert_workflow import AlertState, report

logger = logging.getLogger("ops-agent.retry_workflow")

MAX_RETRY_ROUNDS = 5


def _coerce_round(value: object, default: int = 0) -> int:
    """把回调或数据库里恢复出的轮次转换为安全整数。"""
    try:
        return max(0, int(value or default))
    except (TypeError, ValueError):
        logger.warning("重试轮次解析失败，使用默认值: value=%s, default=%s", value, default)
        return default


def _build_history_entry(state: dict) -> dict:
    """把上一轮执行和验证结果压缩成历史摘要，供 LLM 下一轮自省使用。"""
    current_round = max(1, _coerce_round(state.get("retry_count"), 1))
    previous_plan = state.get("runbook") or {}
    return {
        "round": current_round,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "plan_steps": previous_plan.get("steps", []),
        "execution": state.get("execution_result") or {},
        "verification": state.get("verification_result") or {},
        "analysis": previous_plan.get("failure_analysis")
        or previous_plan.get("retry_reasoning")
        or previous_plan.get("ai_reasoning")
        or "",
    }


def _format_retry_history(retry_history: list[dict]) -> str:
    """生成飞书重试卡片里的历史摘要。"""
    if not retry_history:
        return "暂无历史重试记录"
    lines = []
    for item in retry_history[-5:]:
        plan_steps = item.get("plan_steps") or []
        plan_summary = "；".join(step.get("description", "") for step in plan_steps if isinstance(step, dict))
        verify = item.get("verification") or {}
        lines.append(
            f"- 第 {item.get('round', '?')} 轮：{plan_summary or '未记录方案'}，"
            f"验证结果={'已恢复' if verify.get('recovered') else '未恢复'}"
        )
    return "\n".join(lines)


def _format_verify_condition(runbook: dict) -> str:
    """把 AI 验证条件转成便于飞书展示的一句话。"""
    verification = runbook.get("verification") or {}
    description = verification.get("description") or "未提供验证说明"
    metric = verification.get("metric", "N/A")
    operator = verification.get("operator", "N/A")
    threshold = verification.get("threshold", "N/A")
    return f"{description}（指标: {metric}, 操作符: {operator}, 阈值: {threshold}）"


async def update_incident_retry_state(incident_id: str, runbook: dict, risk_assessment: dict) -> None:
    """把 Phase C 临时重试状态写回 risk_assessment.retry。"""
    logger.info(
        "进入 update_incident_retry_state: incident=%s, retry_count=%s, steps=%s",
        incident_id,
        (risk_assessment.get("retry") or {}).get("count"),
        len(runbook.get("steps") or []),
    )
    async with AsyncSessionLocal() as session:
        await update_incident(
            session,
            incident_id,
            status="pending_approval",
            runbook_name=runbook.get("name"),
            action_plan=runbook.get("steps", []),
            risk_assessment=risk_assessment,
            approval_status="pending",
        )
    logger.info("重试状态已写回工单: incident=%s", incident_id)


async def send_retry_card(state: dict) -> None:
    """发送“继续 AI 执行 / 转人工”的飞书重试卡片。"""
    from agent.channels.feishu import send_card_to_chat
    from agent.templates import render_card
    from agent.tools.cmdb import get_service_chat_id

    incident_id = state.get("incident_id") or ""
    alert = state.get("alert_parsed") or {}
    runbook = state.get("runbook") or {}
    retry_history = state.get("retry_history") or []
    retry_count = max(1, _coerce_round(state.get("retry_count"), 1))
    service = alert.get("service") or (state.get("context") or {}).get("service") or "unknown"
    severity = alert.get("severity", "P3")
    alert_name = alert.get("alertname", "未知")
    logger.info(
        "准备发送 AI 重试卡片: incident=%s, service=%s, retry_count=%s",
        incident_id,
        service,
        retry_count,
    )

    chat_id = await get_service_chat_id(service)
    if not chat_id:
        logger.warning("服务未配置 chat_id，跳过重试卡片: incident=%s, service=%s", incident_id, service)
        return

    card = render_card(
        "retry_card",
        alert_title=f"[{severity}] {service} - {alert_name}",
        retry_round=retry_count,
        failure_reason=runbook.get("failure_analysis") or "上一轮执行或验证未恢复",
        retry_reasoning=runbook.get("retry_reasoning") or runbook.get("ai_reasoning") or "AI 未提供自省说明",
        action_plan=_format_action_plan(runbook),
        verify_condition=_format_verify_condition(runbook),
        retry_history_summary=_format_retry_history(retry_history),
        incident_id=incident_id,
    )
    result = await send_card_to_chat(chat_id, card)
    logger.info(
        "AI 重试卡片已发送: incident=%s, chat_id=%s, code=%s",
        incident_id,
        chat_id,
        result.get("code"),
    )


async def retry_execute(state: AlertState) -> AlertState:
    """执行当前 AI 方案。

    这里不再读取 risk_assessment.allowed 阻断执行，因为“继续 AI 执行”
    本身就是用户对当前 AI 方案的二次确认；真正的安全边界仍由 kubectl
    命令白名单兜住。
    """
    incident_id = state.get("incident_id") or ""
    operator = state.get("operator") or "system"
    runbook = state.get("runbook") or {}
    steps = runbook.get("steps") or []
    round_num = max(1, _coerce_round(state.get("retry_count"), 1))
    logger.info(
        "进入 retry_execute: incident=%s, operator=%s, round=%s, steps=%s",
        incident_id,
        operator,
        round_num,
        len(steps),
    )

    selected_steps = select_executable_steps(steps)
    if not selected_steps:
        reason = "AI 重试方案没有可自动执行的变更步骤"
        state["execution_result"] = {
            "status": "blocked",
            "reason": reason,
            "executed": 0,
            "results": [],
            "round": round_num,
        }
        await write_audit(incident_id, operator, "retry_execution_blocked", {"reason": reason, "round": round_num})
        logger.warning("AI 重试执行被阻断: incident=%s, reason=%s", incident_id, reason)
        return state

    results = []
    await update_incident_status(incident_id, "executing", approval_status="retry_continue")
    for step in selected_steps:
        command = step.get("command", "")
        logger.info(
            "准备执行 AI 重试步骤: incident=%s, round=%s, command=%s",
            incident_id,
            round_num,
            command,
        )
        result = await execute_kubectl(command)
        status = "success" if result.get("exit_code") == 0 else result.get("status", "failed")
        await record_execution(incident_id, command, operator, status, result, round_num=round_num)
        await write_audit(
            incident_id,
            operator,
            "retry_command_executed",
            {"command": command, "status": status, "result": result, "round": round_num},
        )
        results.append({"step": step, "result": result, "status": status})
        if status != "success":
            logger.warning(
                "AI 重试步骤执行失败，停止本轮后续动作: incident=%s, round=%s, command=%s",
                incident_id,
                round_num,
                command,
            )
            break

    success = bool(results) and all(item["status"] == "success" for item in results)
    state["execution_result"] = {
        "status": "success" if success else "failed",
        "executed": len(results),
        "results": results,
        "round": round_num,
    }
    await update_incident_status(
        incident_id,
        "executed" if success else "execution_failed",
        approval_status="retry_continue",
    )
    logger.info(
        "AI 重试执行完成: incident=%s, round=%s, status=%s, executed=%s",
        incident_id,
        round_num,
        state["execution_result"]["status"],
        len(results),
    )
    return state


async def retry_verify(state: AlertState) -> AlertState:
    """验证 AI 重试执行后的恢复情况；未恢复时不直接升级，交给路由决定是否自省。"""
    incident_id = state.get("incident_id") or ""
    alert = state.get("alert_parsed") or {}
    context = state.get("context") or {}
    alert_name = alert.get("alertname", "")
    retry_count = _coerce_round(state.get("retry_count"), 0)
    threshold = _resolve_verification(state, alert_name)
    max_wait = 450 if retry_count >= 3 else 300
    logger.info(
        "进入 retry_verify: incident=%s, alert=%s, retry_count=%s, metric=%s, max=%s",
        incident_id,
        alert_name,
        retry_count,
        threshold["metric"],
        threshold["max"],
    )

    result = await verify_recovery(
        incident_id,
        {**context, "service": context.get("service") or alert.get("service", "unknown")},
        alert_name,
        max_wait=max_wait,
        threshold_override=threshold,
    )
    state["verification_result"] = result
    if result.get("recovered"):
        await update_incident_status(incident_id, "verified")
        await write_audit(incident_id, "system", "retry_recovery_verified", result)
        logger.info("AI 重试验证通过: incident=%s", incident_id)
    else:
        await write_audit(incident_id, "system", "retry_recovery_verify_failed", result)
        logger.warning("AI 重试验证未恢复: incident=%s, result=%s", incident_id, result.get("status"))
    return state


async def retry_analyze(state: AlertState) -> AlertState:
    """重采上下文，调用 LLM 自省上一轮失败，并发送下一轮重试卡片。"""
    incident_id = state.get("incident_id") or ""
    current_retry_count = _coerce_round(state.get("retry_count"), 0)
    next_retry_count = current_retry_count + 1
    logger.info(
        "进入 retry_analyze: incident=%s, current_retry_count=%s, next_retry_count=%s",
        incident_id,
        current_retry_count,
        next_retry_count,
    )

    if next_retry_count > MAX_RETRY_ROUNDS:
        state["error"] = f"AI 重试已达到最大轮次 {MAX_RETRY_ROUNDS}"
        state["approval_status"] = "escalated"
        await write_audit(incident_id, "system", "retry_limit_reached", {"retry_count": current_retry_count})
        logger.warning("AI 重试达到上限: incident=%s, retry_count=%s", incident_id, current_retry_count)
        return state

    retry_history = list(state.get("retry_history") or [])
    retry_history.append(_build_history_entry(state))

    try:
        logger.info("AI 重试自省前重新采集上下文: incident=%s", incident_id)
        refreshed_state = await collect_context_for_incident(state)
        context = refreshed_state.get("context") or state.get("context") or {}
    except Exception as exc:
        state["error"] = f"重试前上下文采集失败: {exc}"
        state["approval_status"] = "escalated"
        await write_audit(incident_id, "system", "retry_context_collect_failed", {"error": str(exc)})
        logger.error("AI 重试上下文采集失败: incident=%s, error=%s", incident_id, exc, exc_info=True)
        return state

    retry_plan = await analyze_failure_and_retry(
        incident_id=incident_id,
        previous_plan=state.get("runbook") or {},
        execution_result=state.get("execution_result") or {},
        verification_result=state.get("verification_result") or {},
        retry_count=next_retry_count,
        retry_history=retry_history,
        context=context,
        alert=state.get("alert_parsed") or {},
    )
    if not retry_plan:
        state["error"] = "AI 重试自省未能生成有效方案"
        state["approval_status"] = "escalated"
        await write_audit(incident_id, "system", "retry_analysis_failed", {"retry_count": next_retry_count})
        logger.warning("AI 重试自省无有效方案: incident=%s, retry_count=%s", incident_id, next_retry_count)
        return state

    new_runbook = {
        "name": "ai_retry",
        "ai_generated": True,
        "ai_reasoning": retry_plan.get("retry_reasoning", ""),
        "retry_reasoning": retry_plan.get("retry_reasoning", ""),
        "failure_analysis": retry_plan.get("failure_analysis", ""),
        "steps": retry_plan.get("steps", []),
        "verification": retry_plan.get("verification", {}),
        "confidence": retry_plan.get("confidence", 0.5),
    }
    risk_assessment = {**(state.get("risk_assessment") or {})}
    risk_assessment.update(
        {
            "ai_generated": True,
            "ai_confidence": retry_plan.get("confidence", 0.5),
            "ai_reasoning": retry_plan.get("retry_reasoning", ""),
            "verification": retry_plan.get("verification", {}),
            "retry": {
                "count": next_retry_count,
                "history": retry_history,
                "latest_plan": new_runbook,
            },
        }
    )

    state["context"] = context
    state["runbook"] = new_runbook
    state["risk_assessment"] = risk_assessment
    state["retry_count"] = next_retry_count
    state["retry_history"] = retry_history
    state["approval_status"] = "pending"

    await update_incident_retry_state(incident_id, new_runbook, risk_assessment)
    await send_retry_card(state)
    await write_audit(
        incident_id,
        "system",
        "retry_plan_generated",
        {
            "retry_count": next_retry_count,
            "failure_analysis": retry_plan.get("failure_analysis", ""),
            "steps": retry_plan.get("steps", []),
        },
    )
    logger.info(
        "AI 重试自省完成并等待用户确认: incident=%s, retry_count=%s, steps=%s",
        incident_id,
        next_retry_count,
        len(new_runbook["steps"]),
    )
    return state


async def retry_escalate(state: AlertState) -> AlertState:
    """AI 重试无法继续时升级人工并同步工单状态。"""
    incident_id = state.get("incident_id") or ""
    reason = (
        state.get("error")
        or (state.get("execution_result") or {}).get("reason")
        or (state.get("verification_result") or {}).get("reason")
        or "AI 重试链路未满足继续条件"
    )
    state["approval_status"] = "escalated"
    await update_incident_status(incident_id, "escalated", approval_status="escalated")
    await write_audit(incident_id, "system", "retry_escalated", {"reason": reason})
    logger.warning("AI 重试升级人工: incident=%s, reason=%s", incident_id, reason)
    return state


def route_after_retry_execute(state: AlertState) -> str:
    """AI 执行后路由：成功去验证，失败则尝试自省，达到上限才升级。"""
    incident_id = state.get("incident_id", "-")
    if state.get("error"):
        logger.warning("[AI重试路由] 执行后发现错误，升级人工: incident=%s", incident_id)
        return "escalate"

    execution_result = state.get("execution_result") or {}
    if execution_result.get("status") == "success":
        logger.info("[AI重试路由] 执行成功，进入恢复验证: incident=%s", incident_id)
        return "retry_verify"

    retry_count = _coerce_round(state.get("retry_count"), 0)
    if retry_count < MAX_RETRY_ROUNDS:
        logger.warning(
            "[AI重试路由] 执行未成功，进入失败自省: incident=%s, retry_count=%s",
            incident_id,
            retry_count,
        )
        return "retry_analyze"

    logger.warning("[AI重试路由] 执行失败且达到上限，升级人工: incident=%s", incident_id)
    return "escalate"


def route_after_retry_verify(state: AlertState) -> str:
    """AI 验证后路由：恢复则报告，未恢复则继续自省或升级。"""
    incident_id = state.get("incident_id", "-")
    if state.get("error"):
        logger.warning("[AI重试路由] 验证后发现错误，升级人工: incident=%s", incident_id)
        return "escalate"

    verification_result = state.get("verification_result") or {}
    if verification_result.get("recovered"):
        logger.info("[AI重试路由] 恢复验证通过，进入报告生成: incident=%s", incident_id)
        return "generate_report"

    retry_count = _coerce_round(state.get("retry_count"), 0)
    if retry_count < MAX_RETRY_ROUNDS:
        logger.warning(
            "[AI重试路由] 恢复验证未通过，进入失败自省: incident=%s, retry_count=%s",
            incident_id,
            retry_count,
        )
        return "retry_analyze"

    logger.warning(
        "[AI重试路由] 恢复验证未通过且达到上限，升级人工: incident=%s, retry_count=%s",
        incident_id,
        retry_count,
    )
    return "escalate"


def route_after_retry_analyze(state: AlertState) -> str:
    """AI 自省后路由：生成卡片后结束本轮，失败则升级。"""
    incident_id = state.get("incident_id", "-")
    if state.get("error"):
        logger.warning("[AI重试路由] 自省失败，升级人工: incident=%s, error=%s", incident_id, state.get("error"))
        return "escalate"
    logger.info("[AI重试路由] 已发送重试卡片，等待用户确认: incident=%s", incident_id)
    return END


def build_retry_workflow() -> StateGraph:
    """构建 Phase C AI 重试工作流。"""
    logger.info("构建 Phase C AI 重试工作流图")
    workflow = StateGraph(AlertState)

    workflow.add_node("retry_execute", retry_execute)
    workflow.add_node("retry_verify", retry_verify)
    workflow.add_node("retry_analyze", retry_analyze)
    workflow.add_node("generate_report", report)
    workflow.add_node("escalate", retry_escalate)

    workflow.set_entry_point("retry_execute")
    workflow.add_conditional_edges(
        "retry_execute",
        route_after_retry_execute,
        {
            "retry_verify": "retry_verify",
            "retry_analyze": "retry_analyze",
            "escalate": "escalate",
        },
    )
    workflow.add_conditional_edges(
        "retry_verify",
        route_after_retry_verify,
        {
            "generate_report": "generate_report",
            "retry_analyze": "retry_analyze",
            "escalate": "escalate",
        },
    )
    workflow.add_conditional_edges(
        "retry_analyze",
        route_after_retry_analyze,
        {
            "escalate": "escalate",
            END: END,
        },
    )
    workflow.add_edge("generate_report", END)
    workflow.add_edge("escalate", END)

    compiled = workflow.compile()
    logger.info("Phase C AI 重试工作流图编译完成")
    return compiled
