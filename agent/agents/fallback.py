import json
import logging

from agent.llm.client import chat_json

logger = logging.getLogger("ops-agent.fallback")

FALLBACK_SYSTEM_PROMPT = """你是一个 SRE 运维专家。当前告警未匹配到任何预置 Runbook，你需要自主分析可观测数据并制定处置方案。

## 分析原则

1. 综合 CPU、内存、QPS、响应时间、错误率等指标，结合 Pod 状态和错误日志，判断根因
2. 优先选择可逆操作（扩容、重启 Pod、回滚部署），严禁不可逆操作（删库、删 PVC、删 ConfigMap）
3. 命令必须是 kubectl 命令，且以以下前缀之一开头（白名单约束）：
   - kubectl scale deployment
   - kubectl delete pod
   - kubectl rollout undo deployment
   - kubectl set resources deployment
   - kubectl get pods
   - kubectl describe pod
4. 每步标注风险等级：[低风险] / [中风险] / [高风险]。高风险步骤不会自动执行，仅作建议
5. 指定验证条件：执行后检查哪个指标 + 判断符（< 或 >）+ 阈值 + 文字说明

## 输出格式

严格输出以下 JSON，不要 markdown 包裹，不要额外解释：

{
  "reasoning": "推理过程（200字以内，说明为什么选择这个方案）",
  "steps": [
    {"risk_level": "低风险", "description": "步骤描述", "command": "kubectl get pods -n demo"}
  ],
  "rollback": "kubectl rollout undo deployment xxx -n demo",
  "verification": {
    "metric": "cpu",
    "operator": "<",
    "threshold": 70.0,
    "description": "CPU 使用率降至 70% 以下"
  },
  "confidence": 0.75
}

## 验证指标的合法取值

metric 只能取: cpu, memory, qps, rt_avg, error_rate
operator 只能取: < 或 >
threshold: cpu 取 0-100 的数字（百分比），memory 取 0-1 的小数（比率），qps 取正数，rt_avg 取正数（秒），error_rate 取 0-1 的小数（比率）"""

VALID_METRICS = {"cpu", "memory", "qps", "rt_avg", "error_rate"}
VALID_OPERATORS = {"<", ">"}
VALID_RISK_LEVELS = {"低风险", "中风险", "高风险"}
WHITELIST_PREFIXES = (
    "kubectl scale deployment",
    "kubectl delete pod",
    "kubectl rollout undo deployment",
    "kubectl set resources deployment",
    "kubectl get pods",
    "kubectl describe pod",
)


def _build_fallback_prompt(context: dict, alert: dict, diagnosis: dict) -> str:
    """构建 AI 兜底方案生成的提示词。"""
    metrics = context.get("metrics", {})
    logs = context.get("logs", [])
    pods = context.get("pods", {})
    cmdb = context.get("cmdb", {})

    cpu = metrics.get("cpu", {})
    memory = metrics.get("memory", {})
    qps = metrics.get("qps", {})
    rt = metrics.get("rt_avg", {})
    error_rate = metrics.get("error_rate", {})

    log_lines = [item.get("line", "") for item in logs[:20]]
    log_text = "\n".join(f"  - {line}" for line in log_lines) if log_lines else "  无错误日志"

    logger.info(
        "构建 AI 兜底 Prompt: alert=%s, service=%s, metrics=%s, logs=%s, pods=%s",
        alert.get("alertname", "未知"),
        alert.get("service", "未知"),
        list(metrics.keys()),
        len(logs),
        pods.get("total", 0),
    )

    return f"""请根据以下可观测数据，自主分析并制定处置方案。

=== 告警信息 ===
- 告警名称: {alert.get('alertname', '未知')}
- 服务: {alert.get('service', '未知')}
- 环境: {alert.get('env', 'prod')}
- 级别: {alert.get('severity', 'P3')}
- 告警值: {alert.get('value', '无')}

=== LLM 初步诊断 ===
- 根因: {diagnosis.get('root_cause', '未知')}

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


def _validate_ai_output(result: dict) -> list[str]:
    """校验 LLM 输出，返回错误列表；空列表表示可以进入风险评估。"""
    errors = []

    if not isinstance(result.get("steps"), list) or not result["steps"]:
        errors.append("steps 为空或不是列表")
        return errors

    verification = result.get("verification", {})
    metric = verification.get("metric", "")
    operator = verification.get("operator", "")
    threshold = verification.get("threshold")

    if metric not in VALID_METRICS:
        errors.append(f"验证指标 {metric} 不在合法范围 {sorted(VALID_METRICS)}")
    if operator not in VALID_OPERATORS:
        errors.append(f"验证操作符 {operator} 不是 < 或 >")
    if not isinstance(threshold, (int, float)):
        errors.append(f"验证阈值 {threshold} 不是数字")

    for index, step in enumerate(result["steps"], start=1):
        command = (step.get("command") or "").strip()
        risk_level = step.get("risk_level")
        description = step.get("description")

        if not command:
            errors.append(f"步骤 {index}: 缺少 command")
        elif not any(command.startswith(prefix) for prefix in WHITELIST_PREFIXES):
            errors.append(f"步骤 {index}: 命令 '{command[:60]}' 不在白名单中")

        if risk_level not in VALID_RISK_LEVELS:
            errors.append(f"步骤 {index}: 风险等级 '{risk_level}' 不合法（应为 低风险/中风险/高风险）")

        if not description:
            errors.append(f"步骤 {index}: 缺少 description")

    return errors


async def generate_ai_action_plan(context: dict, alert: dict, diagnosis: dict) -> dict | None:
    """调用 LLM 为未命中 Runbook 的告警生成 AI 兜底处置方案。"""
    alert_name = alert.get("alertname", "未知")
    service = alert.get("service", "未知")
    logger.info(
        "进入 Fallback Agent: alert=%s, service=%s, has_context=%s, has_diagnosis=%s",
        alert_name,
        service,
        bool(context),
        bool(diagnosis),
    )

    try:
        prompt = _build_fallback_prompt(context, alert, diagnosis)
        logger.info("调用 LLM 自主生成方案: alert=%s, prompt_length=%s", alert_name, len(prompt))
        result = await chat_json(prompt, system=FALLBACK_SYSTEM_PROMPT)
    except Exception as exc:
        logger.error("Fallback Agent LLM 调用失败: alert=%s, error=%s", alert_name, exc, exc_info=True)
        return None

    errors = _validate_ai_output(result)
    if errors:
        logger.warning("Fallback Agent LLM 输出校验失败: alert=%s, errors=%s", alert_name, errors)
        return None

    reasoning = result.get("reasoning", "")
    confidence = float(result.get("confidence", 0.5))
    steps = [
        {
            "risk_level": step["risk_level"],
            "description": step["description"],
            "command": step.get("command", ""),
        }
        for step in result["steps"]
    ]

    logger.info(
        "Fallback Agent 方案生成完成: alert=%s, service=%s, steps=%s, confidence=%s",
        alert_name,
        service,
        len(steps),
        confidence,
    )
    return {
        "name": "ai_fallback",
        "steps": steps,
        "ai_generated": True,
        "ai_reasoning": reasoning,
        "verification": result.get("verification", {}),
        "confidence": confidence,
    }


RETRY_SYSTEM_PROMPT = """你是一个 SRE 故障自愈专家。上一轮 AI 自动处置未能恢复服务，你需要分析失败原因并制定修正方案。

## 分析原则

1. 必须基于上一轮的失败证据：仔细阅读执行结果（stdout/stderr/exit_code）和验证结果（指标名、当前值、阈值）
2. 本轮方案必须不同于前几轮：如果第 1 轮扩容无效，第 2 轮不要再次扩容；尝试不同策略（重启 Pod、回滚部署、调整资源 limit）
3. 考虑间接原因：CPU 高可能是因为下游服务慢导致线程堆积，错误率高可能是因为连接池耗尽而非代码 bug
4. 命令必须是 kubectl 命令，且以以下前缀之一开头：
   - kubectl scale deployment
   - kubectl delete pod
   - kubectl rollout undo deployment
   - kubectl set resources deployment
   - kubectl get pods
   - kubectl describe pod
5. 每步标注风险等级：[低风险] / [中风险] / [高风险]
6. 指定验证条件：执行后检查哪个指标 + 判断符（< 或 >）+ 阈值 + 文字说明

## 输出格式

严格输出以下 JSON，不要 markdown 包裹，不要额外解释：

{
  "retry_reasoning": "本轮修正逻辑（200字以内）",
  "failure_analysis": "上一轮失败原因分析（100字以内）",
  "steps": [
    {"risk_level": "中风险", "description": "步骤描述", "command": "kubectl scale deployment xxx -n demo --replicas=4"}
  ],
  "verification": {
    "metric": "cpu",
    "operator": "<",
    "threshold": 70.0,
    "description": "CPU 使用率降至 70% 以下"
  },
  "confidence": 0.7
}"""


def _build_retry_prompt(
    previous_plan: dict,
    execution_result: dict,
    verification_result: dict,
    retry_count: int,
    retry_history: list[dict],
    context: dict,
    alert: dict,
) -> str:
    """构建重试自省提示词，突出上一轮失败证据和历史尝试。"""
    metrics = context.get("metrics", {})
    pods = context.get("pods", {})

    cpu = metrics.get("cpu", {})
    memory = metrics.get("memory", {})
    qps = metrics.get("qps", {})
    rt = metrics.get("rt_avg", {})
    error_rate = metrics.get("error_rate", {})

    history_lines = []
    for item in retry_history:
        plan_summary = "; ".join(
            step.get("description", "")[:40]
            for step in item.get("plan_steps", [])
            if isinstance(step, dict)
        )
        if not plan_summary:
            plan_summary = "; ".join(str(step)[:40] for step in item.get("plan_steps", []))
        exec_status = item.get("execution", {}).get("status", "?")
        verify_status = "已恢复" if item.get("verification", {}).get("recovered") else "未恢复"
        history_lines.append(
            f"第 {item.get('round', '?')} 轮: {plan_summary or '无方案摘要'} → 执行{exec_status} → {verify_status}"
        )
    history_text = "\n".join(f"  - {line}" for line in history_lines) if history_lines else "  无"

    exec_status = execution_result.get("status", "未知")
    exec_stdout = (execution_result.get("stdout") or "")[:300]
    exec_stderr = (execution_result.get("stderr") or "")[:500]

    verify_metric = verification_result.get("metric", "?")
    verify_current = verification_result.get("current", "?")
    verify_threshold = verification_result.get("threshold", "?")
    verify_recovered = "是" if verification_result.get("recovered") else "否"

    previous_steps = previous_plan.get("steps") or []
    previous_steps_text = "\n".join(
        f"  {index}. [{step.get('risk_level', '?')}] {step.get('description', '')}\n"
        f"     `{step.get('command', '')}`"
        for index, step in enumerate(previous_steps, start=1)
    ) or "  无上一轮方案"

    logger.info(
        "构建重试自省 Prompt: alert=%s, service=%s, retry_count=%s, history=%s",
        alert.get("alertname", "未知"),
        alert.get("service", "未知"),
        retry_count,
        len(retry_history),
    )

    return f"""请分析上一轮 AI 处置失败的原因，并制定第 {retry_count}/5 轮修正方案。

=== 本轮重试信息 ===
- 当前轮次: 第 {retry_count}/5 轮
- 告警名称: {alert.get('alertname', '未知')}
- 服务: {alert.get('service', '未知')}
- 环境: {alert.get('env', 'prod')}
- 级别: {alert.get('severity', 'P3')}

=== 上一轮处置方案 ===
{previous_steps_text}

=== 上一轮执行结果 ===
- 状态: {exec_status}
- stdout: {exec_stdout or '无输出'}
- stderr: {exec_stderr or '无错误'}
- exit_code: {execution_result.get('exit_code', '未知')}

=== 上一轮验证结果 ===
- 指标: {verify_metric}
- 当前值: {verify_current}
- 阈值: {verify_threshold}
- 是否恢复: {verify_recovered}

=== 前几轮历史 ===
{history_text}

=== 当前系统状态（重新采集） ===
- CPU使用率: {cpu.get('current', 0):.1f}%
- 内存使用: {memory.get('current', 0):.0f} bytes
- QPS: {qps.get('current', 0):.1f} req/s
- 平均响应时间: {rt.get('current', 0):.4f}s
- 错误率: {error_rate.get('current', 0):.4f}

=== Pod 状态 ===
- 总数: {pods.get('total', 0)}
- 就绪: {pods.get('ready', 0)}
- Pod 明细: {json.dumps(pods.get('pods', []), ensure_ascii=False) if pods.get('pods') else '无'}"""


async def analyze_failure_and_retry(
    incident_id: str,
    previous_plan: dict,
    execution_result: dict,
    verification_result: dict,
    retry_count: int,
    retry_history: list[dict],
    context: dict,
    alert: dict,
) -> dict | None:
    """调用 LLM 分析上一轮失败原因并生成修正方案。"""
    alert_name = alert.get("alertname", "未知")
    logger.info(
        "进入重试自省: incident=%s, alert=%s, retry_count=%s, history_rounds=%s",
        incident_id,
        alert_name,
        retry_count,
        len(retry_history or []),
    )

    try:
        prompt = _build_retry_prompt(
            previous_plan,
            execution_result,
            verification_result,
            retry_count,
            retry_history or [],
            context,
            alert,
        )
        logger.info("调用 LLM 重试自省: alert=%s, retry_count=%s, prompt_length=%s", alert_name, retry_count, len(prompt))
        result = await chat_json(prompt, system=RETRY_SYSTEM_PROMPT)
    except Exception as exc:
        logger.error("重试自省 LLM 调用失败: alert=%s, error=%s", alert_name, exc, exc_info=True)
        return None

    errors = _validate_ai_output(result)
    if errors:
        logger.warning("重试自省 LLM 输出校验失败: alert=%s, retry_count=%s, errors=%s", alert_name, retry_count, errors)
        return None

    confidence = float(result.get("confidence", 0.5))
    failure_analysis = result.get("failure_analysis", "")
    steps = [
        {
            "risk_level": step["risk_level"],
            "description": step["description"],
            "command": step.get("command", ""),
        }
        for step in result["steps"]
    ]

    logger.info(
        "重试自省完成: alert=%s, retry_count=%s, steps=%s, confidence=%s, analysis=%s",
        alert_name,
        retry_count,
        len(steps),
        confidence,
        failure_analysis[:80],
    )
    return {
        "retry_reasoning": result.get("retry_reasoning", ""),
        "failure_analysis": failure_analysis,
        "steps": steps,
        "verification": result.get("verification", {}),
        "confidence": confidence,
    }
