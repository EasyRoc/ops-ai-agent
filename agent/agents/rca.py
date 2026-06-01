# agent/agents/rca.py
import logging
import json

from agent.db.crud import update_incident, AsyncSessionLocal
from agent.workflows.alert_workflow import AlertState

logger = logging.getLogger("ops-agent.rca")

RCA_SYSTEM_PROMPT = """你是一个 SRE 根因分析专家。根据告警信息和采集到的可观测性数据，分析导致告警的根本原因。

分析原则:
1. 综合 CPU、内存、QPS、响应时间、错误率等指标，找出异常指标之间的因果关系
2. 结合 Pod 状态（就绪数、重启次数）判断是基础设施问题还是应用问题
3. 参考最近的错误日志，定位具体异常点
4. 给出置信度：明确证据 → 0.8+，推测 → 0.5-0.7，信息不足 → 0.3 以下

你必须用中文输出，严格按以下 JSON 格式回复:
{"root_cause": "根因描述（一句话，中文）", "confidence": 0.85, "evidence": ["证据1", "证据2", "证据3"]}"""


def _build_diagnosis_prompt(context: dict, alert: dict) -> str:
    """构建 LLM 诊断提示词"""
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
    """使用 LLM 进行根因分析"""
    context = state.get("context", {})
    alert = state.get("alert_parsed", {})
    incident_id = state.get("incident_id")

    if not context or not incident_id:
        logger.warning("根因分析跳过: 缺少上下文或工单ID")
        return state

    alert_name = alert.get("alertname", "")
    logger.info(f"根因分析: LLM 诊断中 ({alert_name})")

    try:
        diagnosis = await _diagnose_with_llm(context, alert)
    except Exception as e:
        logger.error(f"LLM 诊断失败，回退到规则诊断: {e}")
        diagnosis = _diagnose_fallback(context, alert)

    try:
        await _save_diagnosis(incident_id, diagnosis)
        await _notify_diagnosis(incident_id, diagnosis, alert)
        state["diagnosis"] = diagnosis
        logger.info(
            f"诊断完成: 工单={incident_id}, "
            f"根因={diagnosis['root_cause']}, "
            f"置信度={diagnosis['confidence']}"
        )
    except Exception as e:
        logger.error(f"根因分析失败: {e}", exc_info=True)
        state["error"] = str(e)

    return state


async def _diagnose_with_llm(context: dict, alert: dict) -> dict:
    """调用 LLM 进行根因分析"""
    from agent.llm.client import chat_json

    prompt = _build_diagnosis_prompt(context, alert)
    logger.info(f"LLM 诊断提示词长度: {len(prompt)} 字符")

    result = await chat_json(prompt, system=RCA_SYSTEM_PROMPT)

    # 校验返回格式
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
    """LLM 不可用时的规则兜底诊断"""
    metrics = context.get("metrics", {})
    pods = context.get("pods", {})

    cpu_current = metrics.get("cpu", {}).get("current", 0)
    qps_current = metrics.get("qps", {}).get("current", 0)
    total_pods = pods.get("total", 0)
    ready_pods = pods.get("ready", 0)
    all_healthy = ready_pods == total_pods and total_pods > 0
    alert_name = alert.get("alertname", "未知")

    logger.info(f"规则兜底诊断: cpu={cpu_current:.1f}%, qps={qps_current:.1f}, pod={ready_pods}/{total_pods}")

    if "CPU" in alert_name.upper() and qps_current > 100 and all_healthy:
        return {
            "root_cause": "流量上涨导致服务资源不足",
            "confidence": 0.85,
            "evidence": [f"CPU {cpu_current:.1f}%, QPS {qps_current:.1f}, 所有Pod健康", "判断为流量驱动型资源不足"],
        }
    elif "CPU" in alert_name.upper() and not all_healthy and qps_current < 50:
        return {
            "root_cause": "单实例异常或代码死循环",
            "confidence": 0.70,
            "evidence": [f"仅 {ready_pods}/{total_pods} Pod就绪, QPS {qps_current:.1f}", "判断为单实例故障"],
        }
    else:
        return {
            "root_cause": f"收到 {alert_name} 告警（LLM不可用，规则兜底）",
            "confidence": 0.30,
            "evidence": [f"告警类型: {alert_name}", "LLM 服务不可用，请人工确认"],
        }


async def _save_diagnosis(incident_id: str, diagnosis: dict):
    """Persist root_cause, confidence, evidence and update status to DB."""
    async with AsyncSessionLocal() as session:
        await update_incident(
            session,
            incident_id,
            root_cause=diagnosis.get("root_cause"),
            confidence=diagnosis.get("confidence"),
            evidence=diagnosis.get("evidence"),
            status="diagnosed",
        )
        logger.info(f"诊断结果已保存到数据库: 工单={incident_id}")


async def _notify_diagnosis(incident_id: str, diagnosis: dict, alert: dict):
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

        card = render_card(
            "diagnosis_card",
            alert_title=f"[{severity}] {service} - {alert_name}",
            severity_color=severity_color_map.get(severity, "blue"),
            root_cause=diagnosis.get("root_cause", ""),
            evidence_list="\n".join(diagnosis.get("evidence", [])),
            confidence=f"{diagnosis.get('confidence', 0) * 100:.0f}",
            incident_id=incident_id,
            status="待确认",
            duration="刚刚",
        )

        chat_id = await get_service_chat_id(service)
        if chat_id:
            result = await send_card_to_chat(chat_id, card)
            logger.info(
                f"诊断通知已发送: 群={chat_id}, "
                f"工单={incident_id}, code={result.get('code')}"
            )
        else:
            logger.warning(
                f"服务 '{service}' 未配置 chat_id，"
                f"跳过诊断通知: 工单={incident_id}"
            )
    except Exception as e:
        logger.error(f"诊断通知发送失败: 工单={incident_id}, 错误={e}")
