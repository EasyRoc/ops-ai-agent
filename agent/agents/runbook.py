import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("ops-agent.runbook")

RUNBOOK_DIR = Path(__file__).resolve().parents[2] / "runbooks"

# 告警名可能来自 Prometheus 规则，也可能来自手工测试 payload。
# 这里先做轻量关键词映射，后续如果 Runbook 多起来，可以替换成 YAML 元数据或向量检索。
ALERT_TO_RUNBOOK = {
    "HIGHCPU": "cpu_high.md",
    "CPU": "cpu_high.md",
    "OOMKILLED": "oom.md",
    "OOM": "oom.md",
    "MEMORY": "oom.md",
    "HIGHERRORRATE": "error_rate.md",
    "ERROR_RATE": "error_rate.md",
    "ERROR": "error_rate.md",
    "HIGHLATENCY": "latency_high.md",
    "LATENCY": "latency_high.md",
    "P99": "latency_high.md",
    "RT": "latency_high.md",
}


@dataclass(frozen=True)
class ActionStep:
    """A single actionable line parsed from a Runbook Markdown file."""

    risk_level: str
    description: str
    command: str = ""

    def to_dict(self) -> dict:
        return {
            "risk_level": self.risk_level,
            "description": self.description,
            "command": self.command,
        }


@dataclass(frozen=True)
class Runbook:
    """Structured Runbook content used by RCA and approval cards."""

    name: str
    content: str
    steps: list[ActionStep] = field(default_factory=list)
    rollback: str = ""
    estimated_time: str = ""


def _normalize_alert_name(alert_name: str) -> str:
    """Normalize alert names so HighCPUUsage, high_cpu_usage and HIGH-CPU match alike."""
    return re.sub(r"[^A-Z0-9]", "", alert_name.upper())


def _extract_first_inline_command(text: str) -> str:
    """Extract the first backtick command from a Runbook step, if present."""
    match = re.search(r"`([^`]+)`", text)
    return match.group(1).strip() if match else ""


def _strip_inline_commands(text: str) -> str:
    """Keep descriptions readable in cards while preserving the command separately."""
    return re.sub(r"`([^`]+)`", r"\1", text).strip()


def _parse_runbook(content: str) -> list[ActionStep]:
    """Parse numbered Markdown steps in the form: 1. [风险等级] 描述 `命令`."""
    logger.info("开始解析 Runbook Markdown: content_length=%s", len(content))
    steps = []
    pattern = re.compile(r"^\s*\d+\.\s*\[(.+?)\]\s*(.+?)\s*$", re.MULTILINE)
    for match in pattern.finditer(content):
        raw_description = match.group(2).strip()
        steps.append(
            ActionStep(
                risk_level=match.group(1).strip(),
                description=_strip_inline_commands(raw_description),
                command=_extract_first_inline_command(raw_description),
            )
        )
    logger.info("Runbook Markdown 解析完成: steps=%s", len(steps))
    return steps


def load_runbook(alert_name: str) -> Runbook | None:
    """Load the best matching Runbook for an alert name."""
    normalized = _normalize_alert_name(alert_name)
    filename = None
    logger.info("开始匹配 Runbook: alert_name=%s, normalized=%s", alert_name, normalized)

    # 先匹配更长的关键词，避免 HIGHCPU 被 CPU 抢先命中而影响可读日志。
    for keyword, candidate in sorted(ALERT_TO_RUNBOOK.items(), key=lambda item: len(item[0]), reverse=True):
        if keyword in normalized:
            filename = candidate
            logger.info("Runbook 关键词命中: keyword=%s, file=%s", keyword, filename)
            break

    if not filename:
        logger.warning("未找到匹配的 Runbook: 告警=%s", alert_name)
        return None

    path = RUNBOOK_DIR / filename
    if not path.exists():
        logger.warning("Runbook 文件不存在: %s", path)
        return None

    content = path.read_text(encoding="utf-8")
    runbook = Runbook(name=filename, content=content, steps=_parse_runbook(content))
    logger.info("Runbook 已加载: %s, 步骤数=%s", filename, len(runbook.steps))
    return runbook


def _first_pod_name(pods: dict) -> str:
    """Use the first observed Pod as a safe placeholder for restart-oriented steps."""
    pod_items = pods.get("pods") or []
    if pod_items and isinstance(pod_items[0], dict):
        return pod_items[0].get("name") or "{{pod_name}}"
    return "{{pod_name}}"


def render_runbook(runbook: Runbook, context: dict) -> list[ActionStep]:
    """Render Runbook placeholders with runtime context collected by the supervisor."""
    service = context.get("service") or "unknown"
    namespace = "demo" if context.get("env", "prod") == "prod" else context.get("env", "demo")
    pods = context.get("pods") or {}
    current_replicas = int(pods.get("total") or 2)

    # 这些默认值只用于生成“建议方案”。Phase 2 不执行命令，所以宁可保守、可读。
    replacements = {
        "{{service}}": service,
        "{{namespace}}": namespace,
        "{{replicas}}": str(max(current_replicas * 2, 1)),
        "{{original_replicas}}": str(max(current_replicas, 1)),
        "{{pod_name}}": _first_pod_name(pods),
        "{{new_limit}}": "768Mi",
        "{{original_limit}}": "512Mi",
    }
    logger.info(
        "开始渲染 Runbook: runbook=%s, service=%s, namespace=%s, replicas=%s, pod=%s",
        runbook.name,
        service,
        namespace,
        current_replicas,
        replacements["{{pod_name}}"],
    )

    rendered = []
    for step in runbook.steps:
        description = step.description
        command = step.command
        for placeholder, value in replacements.items():
            description = description.replace(placeholder, value)
            command = command.replace(placeholder, value)
        rendered.append(
            ActionStep(
                risk_level=step.risk_level,
                description=description,
                command=command,
            )
        )
    logger.info("Runbook 渲染完成: runbook=%s, rendered_steps=%s", runbook.name, len(rendered))
    return rendered
