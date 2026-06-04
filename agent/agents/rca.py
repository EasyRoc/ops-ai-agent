# agent/agents/rca.py
import logging
import json

from agent.db.crud import update_incident, AsyncSessionLocal
from agent.workflows.alert_workflow import AlertState

logger = logging.getLogger("ops-agent.rca")

# 这个 prompt 只要求 LLM 输出根因、置信度和证据。
# Runbook 匹配与风险评估由规则模块完成，避免 LLM 直接生成不可控命令。
RCA_SYSTEM_PROMPT = """你是一个 SRE 根因分析专家。根据告警信息和采集到的可观测性数据，分析导致告警的根本原因。

分析原则:
1. 综合 CPU、内存、QPS、响应时间、错误率等指标，找出异常指标之间的因果关系
2. 结合 Pod 状态（就绪数、重启次数）判断是基础设施问题还是应用问题
3. 参考最近的错误日志，定位具体异常点
4. 给出置信度：明确证据 → 0.8+，推测 → 0.5-0.7，信息不足 → 0.3 以下

你必须用中文输出，严格按以下 JSON 格式回复:
{"root_cause": "根因描述（一句话，中文）", "confidence": 0.85, "evidence": ["证据1", "证据2", "证据3"]}"""


def _build_diagnosis_prompt(context: dict, alert: dict) -> str:
    """Build the RCA prompt from collected observability context."""
    metrics = context.get("metrics", {})
    logs = context.get("logs", [])
    pods = context.get("pods", {})
    cmdb = context.get("cmdb", {})

    cpu = metrics.get("cpu", {})
    memory = metrics.get("memory", {})
    qps = metrics.get("qps", {})
    rt = metrics.get("rt_avg", {})
    error_rate = metrics.get("error_rate", {})

    # 只取前20条日志避免 prompt 过长
    log_lines = [l.get("line", "") for l in logs[:20]]
    log_text = "\n".join(f"  - {line}" for line in log_lines) if log_lines else "  无错误日志"
    logger.info(
        "构建诊断 Prompt: alert=%s, service=%s, metrics=%s, logs=%s, pods=%s",
        alert.get("alertname", "未知"),
        alert.get("service", "未知"),
        list(metrics.keys()),
        len(logs),
        pods.get("total", 0),
    )

    return f"""请分析以下告警并给出根因诊断。

=== 告警信息 ===
- 告警名称: {alert.get('alertname', '未知')}
- 服务: {alert.get('service', '未知')}
- 环境: {alert.get('env', 'prod')}
- 级别: {alert.get('severity', 'P3')}
- 告警值: {alert.get('value', '无')}

=== 指标数据 ===
- CPU使用率: {cpu.get('current', 0):.1f}%
- 内存使用: {memory.get('current', 0):.0f} bytes
- QPS: {qps.get('current', 0):.1f} req/s
- 平均响应时间: {rt.get('current', 0):.4f}s
- 错误率: {error_rate.get('current', 0):.4f}

=== Pod 状态 ===
- 总数: {pods.get('total', 0)}
- 就绪: {pods.get('ready', 0)}
- Pod 明细: {json.dumps(pods.get('pods', []), ensure_ascii=False) if pods.get('pods') else '无'}

=== 最近错误日志 ===
{log_text}

=== CMDB 信息 ===
- 负责人: {cmdb.get('owner', '未知')}
- 团队: {cmdb.get('team', '未知')}
- 依赖服务: {', '.join(cmdb.get('dependencies', [])) or '无'}"""


async def analyze_root_cause(state: AlertState) -> AlertState:
    """Run RCA, attach action plan, persist the result, and notify Feishu.

    这是 diagnose 节点的主入口。它刻意分成四段：
    1. LLM / 规则兜底生成根因；
    2. Runbook + Risk 生成处置建议；
    3. 保存 Incident；
    4. 推送诊断卡片。
    """
    context = state.get("context", {})
    alert = state.get("alert_parsed", {})
    incident_id = state.get("incident_id")

    if not context or not incident_id:
        logger.warning(
            "根因分析跳过: has_context=%s, incident_id=%s",
            bool(context),
            incident_id or "-",
        )
        return state

    alert_name = alert.get("alertname", "")
    logger.info(
        "进入根因分析: incident=%s, alert=%s, service=%s, severity=%s",
        incident_id,
        alert_name,
        alert.get("service", "unknown"),
        alert.get("severity", "P3"),
    )

    try:
        diagnosis = await _diagnose_with_llm(context, alert)
    except Exception as e:
        logger.error("LLM 诊断失败，回退到规则诊断: error=%s", e)
        diagnosis = _diagnose_fallback(context, alert)
    logger.info(
        "根因诊断阶段完成: incident=%s, confidence=%s, evidence_count=%s",
        incident_id,
        diagnosis.get("confidence"),
        len(diagnosis.get("evidence", [])),
    )

    # Phase 2 的关键边界：Agent 只产出处置方案和风险结论，不自动执行命令。
    runbook, risk_assessment = _build_action_plan(context, alert)
    if runbook:
        state["runbook"] = runbook
        state["risk_assessment"] = risk_assessment
        state["approval_status"] = "pending"
        logger.info(
            "处置方案已生成: incident=%s, runbook=%s, steps=%s, risk=%s/%s",
            incident_id,
            runbook.get("name"),
            len(runbook.get("steps", [])),
            risk_assessment.get("level") if risk_assessment else "-",
            risk_assessment.get("score") if risk_assessment else "-",
        )
    else:
        logger.info("未生成处置方案: incident=%s, alert=%s", incident_id, alert_name)

    try:
        await _save_diagnosis(
            incident_id,
            diagnosis,
            runbook=state.get("runbook"),
            risk_assessment=state.get("risk_assessment"),
            approval_status=state.get("approval_status"),
        )
        await _notify_diagnosis(
            incident_id,
            diagnosis,
            alert,
            runbook=state.get("runbook"),
            risk_assessment=state.get("risk_assessment"),
        )
        state["diagnosis"] = diagnosis
        logger.info(
            "诊断完成: incident=%s, root_cause=%s, confidence=%s, approval_status=%s",
            incident_id,
            diagnosis["root_cause"],
            diagnosis["confidence"],
            state.get("approval_status") or "-",
        )
    except Exception as e:
        logger.error("根因分析结果保存或通知失败: incident=%s, error=%s", incident_id, e, exc_info=True)
        state["error"] = str(e)

    return state


def _build_action_plan(context: dict, alert: dict) -> tuple[dict | None, dict | None]:
    """Build a structured Runbook action plan and its risk assessment."""
    from agent.agents.risk import evaluate_risk
    from agent.agents.runbook import load_runbook, render_runbook

    alert_name = alert.get("alertname", "")
    logger.info(
        "开始生成处置方案: alert=%s, service=%s, env=%s",
        alert_name,
        alert.get("service", "unknown"),
        alert.get("env", "prod"),
    )
    runbook = load_runbook(alert_name)
    if not runbook:
        logger.info("处置方案生成跳过: 未匹配 Runbook, alert=%s", alert_name)
        return None, None

    rendered_steps = render_runbook(
        runbook,
        {
            "service": alert.get("service", ""),
            "env": alert.get("env", "prod"),
            "pods": context.get("pods", {}),
        },
    )
    risk_assessment = evaluate_risk(
        rendered_steps,
        alert.get("severity", "P3"),
        alert.get("service", ""),
        alert.get("env", "prod"),
    )
    logger.info(
        "处置方案生成完成: runbook=%s, steps=%s, risk_level=%s, allowed=%s",
        runbook.name,
        len(rendered_steps),
        risk_assessment.get("level"),
        risk_assessment.get("allowed"),
    )
    return {
        "name": runbook.name,
        "steps": [step.to_dict() for step in rendered_steps],
    }, risk_assessment


async def _diagnose_with_llm(context: dict, alert: dict) -> dict:
    """Call the LLM and normalize its JSON response into the diagnosis schema."""
    from agent.llm.client import chat_json

    prompt = _build_diagnosis_prompt(context, alert)
    logger.info("调用 LLM 根因分析: alert=%s, prompt_length=%s", alert.get("alertname"), len(prompt))

    result = await chat_json(prompt, system=RCA_SYSTEM_PROMPT)

    # LLM 返回内容必须归一化，避免下游卡片渲染和数据库保存拿到缺失字段。
    diagnosis = {
        "root_cause": result.get("root_cause", "LLM 未返回有效诊断"),
        "confidence": float(result.get("confidence", 0.3)),
        "evidence": result.get("evidence", ["LLM 诊断结果异常"]),
    }
    logger.info(
        f"LLM 诊断结果: 根因={diagnosis['root_cause']}, 置信度={diagnosis['confidence']}"
    )
    return diagnosis


def _diagnose_fallback(context: dict, alert: dict) -> dict:
    """Rule-based RCA used when the LLM call fails or is not configured."""
    metrics = context.get("metrics", {})
    pods = context.get("pods", {})

    cpu_current = metrics.get("cpu", {}).get("current", 0)
    qps_current = metrics.get("qps", {}).get("current", 0)
    total_pods = pods.get("total", 0)
    ready_pods = pods.get("ready", 0)
    all_healthy = ready_pods == total_pods and total_pods > 0
    alert_name = alert.get("alertname", "未知")

    logger.info(
        "进入规则兜底诊断: alert=%s, cpu=%.1f%%, qps=%.1f, pod=%s/%s",
        alert_name,
        cpu_current,
        qps_current,
        ready_pods,
        total_pods,
    )

    if "CPU" in alert_name.upper() and qps_current > 100 and all_healthy:
        logger.info("规则兜底命中: 流量上涨导致资源不足")
        return {
            "root_cause": "流量上涨导致服务资源不足",
            "confidence": 0.85,
            "evidence": [f"CPU {cpu_current:.1f}%, QPS {qps_current:.1f}, 所有Pod健康", "判断为流量驱动型资源不足"],
        }
    elif "CPU" in alert_name.upper() and not all_healthy and qps_current < 50:
        logger.info("规则兜底命中: 单实例异常或代码死循环")
        return {
            "root_cause": "单实例异常或代码死循环",
            "confidence": 0.70,
            "evidence": [f"仅 {ready_pods}/{total_pods} Pod就绪, QPS {qps_current:.1f}", "判断为单实例故障"],
        }
    else:
        logger.info("规则兜底未命中强规则: 使用低置信度人工确认结果")
        return {
            "root_cause": f"收到 {alert_name} 告警（LLM不可用，规则兜底）",
            "confidence": 0.30,
            "evidence": [f"告警类型: {alert_name}", "LLM 服务不可用，请人工确认"],
        }


async def _save_diagnosis(
    incident_id: str,
    diagnosis: dict,
    runbook: dict | None = None,
    risk_assessment: dict | None = None,
    approval_status: str | None = None,
):
    """Persist diagnosis and Phase 2 action-plan fields to the Incident row."""
    updates = {
        "root_cause": diagnosis.get("root_cause"),
        "confidence": diagnosis.get("confidence"),
        "evidence": diagnosis.get("evidence"),
        "status": "pending_approval" if approval_status == "pending" else "diagnosed",
    }
    if runbook:
        updates["runbook_name"] = runbook.get("name")
        updates["action_plan"] = runbook.get("steps", [])
    if risk_assessment:
        updates["risk_assessment"] = risk_assessment
    if approval_status:
        updates["approval_status"] = approval_status

    logger.info(
        "保存诊断结果: incident=%s, status=%s, fields=%s",
        incident_id,
        updates.get("status"),
        list(updates.keys()),
    )
    async with AsyncSessionLocal() as session:
        await update_incident(session, incident_id, **updates)
        logger.info("诊断结果已保存到数据库: incident=%s", incident_id)


async def _notify_diagnosis(
    incident_id: str,
    diagnosis: dict,
    alert: dict,
    runbook: dict | None = None,
    risk_assessment: dict | None = None,
):
    """Push a diagnosis result card to the service's Feishu chat."""
    from agent.channels.feishu import send_card_to_chat
    from agent.templates import render_card
    from agent.tools.cmdb import get_service_chat_id

    severity_color_map = {
        "P0": "red",
        "P1": "orange",
        "P2": "yellow",
        "P3": "blue",
    }

    try:
        service = alert.get("service", "unknown")
        severity = alert.get("severity", "P3")
        alert_name = alert.get("alertname", "未知")
        action_plan = _format_action_plan(runbook)
        risk_summary = risk_assessment or {}
        logger.info(
            "准备发送诊断卡片: incident=%s, service=%s, runbook=%s, risk=%s",
            incident_id,
            service,
            runbook.get("name") if runbook else "-",
            risk_summary.get("level", "未评估"),
        )

        card = render_card(
            "diagnosis_card",
            alert_title=f"[{severity}] {service} - {alert_name}",
            severity_color=severity_color_map.get(severity, "blue"),
            root_cause=diagnosis.get("root_cause", ""),
            action_plan=action_plan,
            risk_level=risk_summary.get("level", "未评估"),
            risk_score=str(risk_summary.get("score", 0)),
            risk_warnings=_format_risk_warnings(risk_summary),
            evidence_list="\n".join(diagnosis.get("evidence", [])),
            confidence=f"{diagnosis.get('confidence', 0) * 100:.0f}",
            incident_id=incident_id,
            status="待审批" if runbook else "待确认",
            duration="刚刚",
        )

        chat_id = await get_service_chat_id(service)
        if chat_id:
            result = await send_card_to_chat(chat_id, card)
            logger.info(
                "诊断通知已发送: chat_id=%s, incident=%s, code=%s",
                chat_id,
                incident_id,
                result.get("code"),
            )
        else:
            logger.warning(
                "服务未配置 chat_id，跳过诊断通知: service=%s, incident=%s",
                service,
                incident_id,
            )
    except Exception as e:
        logger.error("诊断通知发送失败: incident=%s, error=%s", incident_id, e)


def _format_action_plan(runbook: dict | None) -> str:
    """Format structured Runbook steps into Feishu markdown."""
    if not runbook:
        return "未匹配到 Runbook，请人工确认处置方案。"
    lines = []
    for index, step in enumerate(runbook.get("steps", []), start=1):
        line = f"{index}. [{step.get('risk_level', '-')}] {step.get('description', '')}"
        if step.get("command"):
            line = f"{line}\n`{step['command']}`"
        lines.append(line)
    return "\n".join(lines) if lines else "Runbook 未提供处置步骤。"


def _format_risk_warnings(risk_assessment: dict) -> str:
    """Prefer explicit warnings; fall back to explanatory factors for the card."""
    warnings = risk_assessment.get("warnings") or []
    factors = risk_assessment.get("factors") or []
    lines = warnings or factors
    return "\n".join(f"- {line}" for line in lines) if lines else "- 无额外风险提示"
