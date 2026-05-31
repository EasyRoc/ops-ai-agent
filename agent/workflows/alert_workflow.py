from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END


class AlertState(TypedDict):
    alert_raw: dict
    incident_id: Optional[str]
    alert_parsed: Optional[dict]
    context: Optional[dict]
    diagnosis: Optional[dict]
    error: Optional[str]


def parse_alert(state: AlertState) -> AlertState:
    """Node: parse alert, create Incident"""
    from agent.agents.alert import parse_and_create_incident
    return parse_and_create_incident(state)


def collect_context(state: AlertState) -> AlertState:
    """Node: collect observability context"""
    from agent.agents.supervisor import collect_context_for_incident
    return collect_context_for_incident(state)


def diagnose(state: AlertState) -> AlertState:
    """Node: root cause analysis"""
    from agent.agents.rca import analyze_root_cause
    return analyze_root_cause(state)


def should_continue(state: AlertState) -> str:
    if state.get("error"):
        return END
    if state.get("diagnosis"):
        return END
    if state.get("context"):
        return "diagnose"
    if state.get("incident_id"):
        return "collect_context"
    return "parse_alert"


def build_alert_workflow() -> StateGraph:
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

    return workflow.compile()
