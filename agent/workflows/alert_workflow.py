import logging
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

logger = logging.getLogger("ops-agent.workflow")

class AlertState(TypedDict):
    alert_raw: dict
    incident_id: Optional[str]
    alert_parsed: Optional[dict]
    context: Optional[dict]
    diagnosis: Optional[dict]
    error: Optional[str]


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


def should_continue(state: AlertState) -> str:
    incident_id = state.get("incident_id", "?")
    if state.get("error"):
        logger.warning(f"[路由] 检测到错误，终止工作流 (incident={incident_id})")
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


def build_alert_workflow() -> StateGraph:
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
