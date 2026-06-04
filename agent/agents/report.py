import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

from agent.agents.audit import write_audit
from agent.db.crud import AsyncSessionLocal, create_report, update_incident
from agent.db.models import Report

logger = logging.getLogger("ops-agent.report")

REPORT_SYSTEM_PROMPT = """你是 SRE 故障复盘助手。请根据输入的 Incident 状态生成简洁、可执行的中文摘要。

必须返回 JSON:
{"summary": "一句话总结", "impact": "影响范围", "suggestions": ["建议1", "建议2", "建议3"]}"""


@dataclass
class IncidentReport:
    """报告对象的内存结构，最终会渲染成 Markdown 并写入 reports 表。"""

    incident_id: str
    title: str
    timeline: list[dict]
    root_cause: str
    confidence: float
    evidence: list[str]
    action_plan: list[dict]
    execution_result: dict
    verification: dict
    suggestions: list[str]
    created_at: str


def _format_json_block(data: dict | list | None) -> str:
    """把结构化字段格式化成 Markdown 代码块，避免报告里丢上下文。"""
    if not data:
        return "无"
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"


def _build_timeline(state: dict) -> list[dict]:
    """根据当前 state 生成轻量时间线；真实系统可扩展为从 audit_logs 回放。"""
    now = datetime.now(timezone.utc).isoformat()
    timeline = [
        {"time": now, "event": "告警进入诊断流程"},
        {"time": now, "event": "根因分析完成"},
    ]
    if state.get("execution_result"):
        timeline.append({"time": now, "event": "自动执行完成"})
    if state.get("verification_result"):
        timeline.append({"time": now, "event": "恢复验证完成"})
    logger.info("报告时间线已构建: incident=%s, events=%s", state.get("incident_id"), len(timeline))
    return timeline


def build_markdown_report(state: dict, summary: dict) -> str:
    """将 Incident 全量状态渲染为 Markdown 故障报告。"""
    incident_id = state.get("incident_id") or "-"
    alert = state.get("alert_parsed") or {}
    diagnosis = state.get("diagnosis") or {}
    runbook = state.get("runbook") or {}
    execution_result = state.get("execution_result") or {}
    verification_result = state.get("verification_result") or {}
    suggestions = summary.get("suggestions") or ["补充监控阈值", "复盘 Runbook 有效性", "沉淀容量基线"]
    report = IncidentReport(
        incident_id=incident_id,
        title=f"{alert.get('service', 'unknown')} - {alert.get('alertname', 'Incident')}",
        timeline=_build_timeline(state),
        root_cause=diagnosis.get("root_cause", "未给出根因"),
        confidence=float(diagnosis.get("confidence", 0) or 0),
        evidence=diagnosis.get("evidence") or [],
        action_plan=runbook.get("steps") or [],
        execution_result=execution_result,
        verification=verification_result,
        suggestions=suggestions,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    logger.info(
        "开始构建 Markdown 报告: incident=%s, title=%s, suggestions=%s",
        incident_id,
        report.title,
        len(suggestions),
    )

    timeline_text = "\n".join(f"- {item['time']}：{item['event']}" for item in report.timeline)
    evidence_text = "\n".join(f"- {item}" for item in report.evidence) if report.evidence else "- 无"
    action_text = "\n".join(
        f"{idx}. {step.get('description', '-')}"
        + (f"\n   `{step.get('command')}`" if step.get("command") else "")
        for idx, step in enumerate(report.action_plan, start=1)
    ) or "无自动处置步骤"
    suggestion_text = "\n".join(f"- {item}" for item in suggestions)

    markdown = f"""# Incident Report: {incident_id}

## 摘要
{summary.get("summary", "本次故障已完成自动处置与验证。")}

**影响范围**：{summary.get("impact", alert.get("service", "未知"))}

## 时间线
{timeline_text}

## 根因
{report.root_cause}

**置信度**：{report.confidence:.0%}

**证据**
{evidence_text}

## 处置方案
Runbook：{runbook.get("name", "未匹配")}

{action_text}

## 执行结果
{_format_json_block(execution_result)}

## 验证结果
{_format_json_block(verification_result)}

## 后续建议
{suggestion_text}
"""
    logger.info("Markdown 报告构建完成: incident=%s, length=%s", incident_id, len(markdown))
    return markdown


async def summarize_with_llm(state: dict) -> dict:
    """调用 LLM 生成报告摘要，失败时回退到规则摘要。"""
    from agent.llm.client import chat_json

    incident_id = state.get("incident_id") or "-"
    logger.info("进入 summarize_with_llm: incident=%s", incident_id)
    prompt = json.dumps(state, ensure_ascii=False, default=str)
    try:
        result = await chat_json(prompt, system=REPORT_SYSTEM_PROMPT)
        summary = {
            "summary": result.get("summary", "故障已处理"),
            "impact": result.get("impact", state.get("alert_parsed", {}).get("service", "未知")),
            "suggestions": result.get("suggestions") or ["补充复盘结论"],
        }
        logger.info("LLM 报告摘要完成: incident=%s", incident_id)
        return summary
    except Exception as exc:
        logger.error("LLM 报告摘要失败，使用规则兜底: incident=%s, error=%s", incident_id, exc)
        service = (state.get("alert_parsed") or {}).get("service", "未知服务")
        recovered = (state.get("verification_result") or {}).get("recovered")
        return {
            "summary": f"{service} 告警已{'恢复' if recovered else '进入人工升级'}，自动化链路已记录处置过程。",
            "impact": service,
            "suggestions": ["补充压测和容量基线", "复查告警阈值", "完善 Runbook 回滚步骤"],
        }


def extract_fault_patterns(state: dict) -> dict:
    """提取可用于历史故障库检索的结构化标签。"""
    alert = state.get("alert_parsed") or {}
    diagnosis = state.get("diagnosis") or {}
    patterns = {
        "service": alert.get("service"),
        "alertname": alert.get("alertname"),
        "root_cause": diagnosis.get("root_cause"),
        "runbook": (state.get("runbook") or {}).get("name"),
        "recovered": (state.get("verification_result") or {}).get("recovered"),
    }
    logger.info("故障模式已提取: incident=%s, patterns=%s", state.get("incident_id"), patterns)
    return patterns


async def save_report(incident_id: str, content: str, fault_patterns: dict | None = None) -> Report:
    """保存 Markdown 报告和结构化故障标签。"""
    logger.info("进入 save_report: incident=%s, content_length=%s", incident_id, len(content))
    async with AsyncSessionLocal() as session:
        saved = await create_report(
            session,
            Report(
                incident_id=incident_id,
                content=content,
                fault_patterns=fault_patterns or {},
            ),
        )
    logger.info("报告保存完成: incident=%s, report_id=%s", incident_id, saved.id)
    return saved


async def _mark_incident_resolved_if_needed(incident_id: str, verification: dict) -> None:
    """验证恢复后把 Incident 标记为 resolved。"""
    if not verification.get("recovered"):
        logger.info("报告节点不关闭工单: incident=%s, recovered=False", incident_id)
        return
    try:
        async with AsyncSessionLocal() as session:
            await update_incident(
                session,
                incident_id,
                status="resolved",
                resolved_at=datetime.now(timezone.utc),
            )
        logger.info("报告节点已关闭工单: incident=%s", incident_id)
    except Exception as exc:
        logger.error("报告节点关闭工单失败: incident=%s, error=%s", incident_id, exc, exc_info=True)


async def generate_incident_report(state: dict) -> dict:
    """生成、保存并回填故障报告。"""
    incident_id = state.get("incident_id") or ""
    logger.info("进入 generate_incident_report: incident=%s", incident_id)
    summary = await summarize_with_llm(state)
    content = build_markdown_report(state, summary)
    fault_patterns = extract_fault_patterns(state)
    await save_report(incident_id, content, fault_patterns)
    await write_audit(
        incident_id,
        "system",
        "report_generated",
        {"summary": summary, "fault_patterns": fault_patterns},
    )
    await _mark_incident_resolved_if_needed(incident_id, state.get("verification_result") or {})
    state["report"] = {
        "incident_id": incident_id,
        "content": content,
        "summary": summary,
        "fault_patterns": fault_patterns,
        "raw": asdict(
            IncidentReport(
                incident_id=incident_id,
                title=((state.get("alert_parsed") or {}).get("alertname") or "Incident"),
                timeline=_build_timeline(state),
                root_cause=(state.get("diagnosis") or {}).get("root_cause", ""),
                confidence=float((state.get("diagnosis") or {}).get("confidence", 0) or 0),
                evidence=(state.get("diagnosis") or {}).get("evidence") or [],
                action_plan=(state.get("runbook") or {}).get("steps") or [],
                execution_result=state.get("execution_result") or {},
                verification=state.get("verification_result") or {},
                suggestions=summary.get("suggestions") or [],
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        ),
    }
    logger.info("报告节点完成: incident=%s, content_length=%s", incident_id, len(content))
    return state


async def report(state: dict) -> dict:
    """LangGraph 节点入口：生成故障报告并沉淀历史模式。"""
    logger.info("进入 report 节点: incident=%s", state.get("incident_id", "-"))
    return await generate_incident_report(state)
