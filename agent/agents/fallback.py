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
