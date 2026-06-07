import logging
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

logger = logging.getLogger("ops-agent.workflow")

class AlertState(TypedDict):
    """告警处理工作流的状态，在各节点间流转"""
    alert_raw: dict            # Alertmanager 原始告警数据（labels、annotations、fingerprint 等）
    incident_id: Optional[str]  # 工单ID，parse_alert 节点去重后创建并回填
    alert_parsed: Optional[dict] # 解析后的告警结构化字段（service、env、severity 等）
    duplicate_alert: Optional[bool] # 告警去重命中标记；为 True 时不再触发诊断和通知
    context: Optional[dict]     # collect_context 采集的可观测性数据（metrics、logs、pods、cmdb）
    diagnosis: Optional[dict]   # diagnose 节点输出的根因分析结果（root_cause、confidence、evidence）
    runbook: Optional[dict]     # Phase 2: 匹配到的 Runbook 和结构化处置步骤
    risk_assessment: Optional[dict]  # Phase 2: 处置方案风险评估
    approval_status: Optional[str]   # Phase 2: pending / approved / rejected / escalated
    execution_result: Optional[dict]  # Phase 3: 自动执行结果
    verification_result: Optional[dict]  # Phase 3: 执行后的恢复验证结果
    retry_count: Optional[int]  # Phase C: 当前 AI 重试轮次，0/None 表示尚未进入重试
    retry_history: Optional[list[dict]]  # Phase C: AI 重试历史摘要，临时存储在 risk_assessment.retry 中
    report: Optional[dict]  # Phase 3: 故障报告生成结果
    operator: Optional[str]  # Phase 3: 审批/执行操作人
    error: Optional[str]        # 任意节点异常时写入，触发工作流提前终止


async def parse_alert(state: AlertState) -> AlertState:
    """Node: parse alert, create Incident"""
    from agent.agents.alert import parse_and_create_incident
    alert_name = state["alert_raw"].get("alertname", "unknown")
    logger.info(f"[解析告警] 开始解析: {alert_name}")
    result = await parse_and_create_incident(state)
    if result.get("error"):
        logger.error(f"[解析告警] 失败: {result['error']}")
    else:
        logger.info(f"[解析告警] 完成, incident_id={result.get('incident_id')}")
    return result


async def collect_context(state: AlertState) -> AlertState:
    """Node: collect observability context"""
    from agent.agents.supervisor import collect_context_for_incident
    service = state.get("alert_parsed", {}).get("service", "unknown")
    logger.info(f"[采集上下文] 正在采集服务 {service} 的上下文数据")
    result = await collect_context_for_incident(state)
    if result.get("error"):
        logger.error(f"[采集上下文] 失败: {result['error']}")
    else:
        ctx = result.get("context", {})
        logger.info(
            f"[采集上下文] 完成: 指标={len(ctx.get('metrics', {}))} 项, "
            f"日志={len(ctx.get('logs', []))} 条, "
            f"Pod={ctx.get('pods', {}).get('total', 0)} 个"
        )
    return result


async def diagnose(state: AlertState) -> AlertState:
    """Node: root cause analysis"""
    from agent.agents.rca import analyze_root_cause
    alert_name = state.get("alert_parsed", {}).get("alertname", "unknown")
    logger.info(f"[根因分析] 开始分析告警: {alert_name}")
    result = await analyze_root_cause(state)
    if result.get("error"):
        logger.error(f"[根因分析] 失败: {result['error']}")
    elif result.get("diagnosis"):
        d = result["diagnosis"]
        logger.info(
            f"[根因分析] 完成: 根因={d.get('root_cause', '?')}, "
            f"置信度={d.get('confidence', 0):.0%}"
        )
    return result


async def execute(state: AlertState) -> AlertState:
    """Node: execute approved runbook action.

    审批通过后自动执行白名单内的 kubectl 命令，结果写入 execution_result。
    """
    from agent.agents.executor import execute as execute_node

    logger.info("[自动执行] 进入执行节点: incident=%s", state.get("incident_id", "-"))
    result = await execute_node(state)
    execution_result = result.get("execution_result") or {}
    logger.info(
        "[自动执行] 执行节点完成: incident=%s, status=%s, executed=%s",
        result.get("incident_id", "-"),
        execution_result.get("status", "-"),
        execution_result.get("executed", 0),
    )
    return result


async def verify(state: AlertState) -> AlertState:
    """Node: verify recovery after execution.

    轮询 Prometheus 指标判断执行后是否恢复，超时或未恢复则后续路由到 escalate。
    """
    from agent.agents.verify import verify as verify_node

    logger.info("[恢复验证] 进入验证节点: incident=%s", state.get("incident_id", "-"))
    result = await verify_node(state)
    verification_result = result.get("verification_result") or {}
    logger.info(
        "[恢复验证] 验证节点完成: incident=%s, recovered=%s, status=%s",
        result.get("incident_id", "-"),
        verification_result.get("recovered"),
        verification_result.get("status", "-"),
    )
    return result


async def report(state: AlertState) -> AlertState:
    """Node: generate incident report.

    汇总告警→诊断→执行→验证全链路数据，调用 LLM 生成故障报告，写入 reports 表。
    """
    from agent.agents.report import report as report_node

    logger.info("[报告沉淀] 进入报告节点: incident=%s", state.get("incident_id", "-"))
    result = await report_node(state)
    logger.info(
        "[报告沉淀] 报告节点完成: incident=%s, has_report=%s",
        result.get("incident_id", "-"),
        bool(result.get("report")),
    )
    return result


async def escalate(state: AlertState) -> AlertState:
    """Node: stop automation and keep the incident in manual escalation.

    执行失败、验证超时或任一节点异常时进入此节点，将审批状态置为 escalated，终止自动链路。
    """
    incident_id = state.get("incident_id") or "-"
    reason = (
        state.get("error")
        or (state.get("execution_result") or {}).get("reason")
        or (state.get("verification_result") or {}).get("reason")
        or "自动执行链路未满足继续条件"
    )
    state["approval_status"] = "escalated"
    logger.warning("[人工升级] incident=%s, reason=%s", incident_id, reason)
    return state


def should_continue(state: AlertState) -> str:
    """Route to the next node based on which state fields have been filled."""
    incident_id = state.get("incident_id", "?")
    if state.get("error"):
        logger.warning(f"[路由] 检测到错误，终止工作流 (incident={incident_id})")
        return END
    if state.get("duplicate_alert"):
        logger.info(f"[路由] 重复告警命中，复用已有工单并终止工作流 (incident={incident_id})")
        return END
    if state.get("diagnosis"):
        logger.info(f"[路由] 诊断完成，终止工作流 (incident={incident_id})")
        return END
    if state.get("context"):
        logger.info(f"[路由] 上下文已采集，路由到根因分析 (incident={incident_id})")
        return "diagnose"
    if state.get("incident_id"):
        logger.info(f"[路由] 工单已创建，路由到采集上下文 (incident={incident_id})")
        return "collect_context"
    logger.info("[路由] 开始处理，路由到解析告警")
    return "parse_alert"


def route_after_execute(state: AlertState) -> str:
    """执行节点之后的路由判断。

    执行成功 → verify 验证节点；
    执行失败 / 有 error → escalate 人工升级。
    """
    incident_id = state.get("incident_id", "-")
    if state.get("error"):
        logger.warning("[路由] 执行后发现错误，升级人工: incident=%s", incident_id)
        return "escalate"
    execution_result = state.get("execution_result") or {}
    if execution_result.get("status") == "success":
        logger.info("[路由] 执行成功，进入恢复验证: incident=%s", incident_id)
        return "verify"
    logger.warning(
        "[路由] 执行未成功，升级人工: incident=%s, status=%s",
        incident_id,
        execution_result.get("status", "-"),
    )
    return "escalate"


def route_after_verify(state: AlertState) -> str:
    """验证节点之后的路由判断。

    恢复验证通过 → generate_report 报告生成节点；
    验证超时 / 未恢复 → escalate 人工升级。
    """
    incident_id = state.get("incident_id", "-")
    verification_result = state.get("verification_result") or {}
    if verification_result.get("recovered"):
        logger.info("[路由] 恢复验证通过，进入报告生成: incident=%s", incident_id)
        return "generate_report"
    logger.warning(
        "[路由] 恢复验证未通过，升级人工: incident=%s, status=%s",
        incident_id,
        verification_result.get("status", "-"),
    )
    return "escalate"


def build_alert_workflow() -> StateGraph:
    """Compile the three-node alert workflow used by webhook background tasks."""
    logger.info("构建告警工作流图")
    workflow = StateGraph(AlertState)

    workflow.add_node("parse_alert", parse_alert)
    workflow.add_node("collect_context", collect_context)
    workflow.add_node("diagnose", diagnose)

    workflow.set_entry_point("parse_alert")
    workflow.add_conditional_edges("parse_alert", should_continue, {
        "collect_context": "collect_context",
        END: END,
    })
    workflow.add_conditional_edges("collect_context", should_continue, {
        "diagnose": "diagnose",
        END: END,
    })
    workflow.add_edge("diagnose", END)

    compiled = workflow.compile()
    logger.info("告警工作流图编译完成")
    return compiled


def build_execution_workflow() -> StateGraph:
    """构建 Phase 3 执行工作流（审批通过后触发）。

    工作流链路：execute → verify → generate_report → END
    异常分支：execute/verify 任一失败 → escalate → END

    当前仅执行白名单内的低/中风险命令，高风险或非白名单命令仍需人工处理。
    """
    logger.info("构建 Phase 3 执行工作流图")
    workflow = StateGraph(AlertState)

    # 注册四个节点
    workflow.add_node("execute", execute)           # 自动执行白名单命令
    workflow.add_node("verify", verify)             # 轮询指标验证恢复
    workflow.add_node("generate_report", report)     # 生成故障报告
    workflow.add_node("escalate", escalate)          # 终止自动链路，转人工

    # 入口：审批通过后从 execute 开始
    workflow.set_entry_point("execute")
    workflow.add_conditional_edges(
        "execute",
        route_after_execute,
        {
            "verify": "verify",       # 执行成功 → 验证
            "escalate": "escalate",   # 执行失败 → 人工
        },
    )
    workflow.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "generate_report": "generate_report",  # 验证通过 → 报告
            "escalate": "escalate",                # 验证失败 → 人工
        },
    )
    # 报告生成后工作流结束
    workflow.add_edge("generate_report", END)
    # 人工升级后工作流结束
    workflow.add_edge("escalate", END)

    compiled = workflow.compile()
    logger.info("Phase 3 执行工作流图编译完成")
    return compiled
