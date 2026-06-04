import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("ops-agent.runbook")

RUNBOOK_DIR = Path(__file__).resolve().parents[2] / "runbooks"

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
    name: str
    content: str
    steps: list[ActionStep] = field(default_factory=list)
    rollback: str = ""
    estimated_time: str = ""


def _normalize_alert_name(alert_name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", alert_name.upper())


def _extract_first_inline_command(text: str) -> str:
    match = re.search(r"`([^`]+)`", text)
    return match.group(1).strip() if match else ""


def _strip_inline_commands(text: str) -> str:
    return re.sub(r"`([^`]+)`", r"\1", text).strip()


def _parse_runbook(content: str) -> list[ActionStep]:
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
    return steps


def load_runbook(alert_name: str) -> Runbook | None:
    normalized = _normalize_alert_name(alert_name)
    filename = None

    for keyword, candidate in sorted(ALERT_TO_RUNBOOK.items(), key=lambda item: len(item[0]), reverse=True):
        if keyword in normalized:
            filename = candidate
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
    pod_items = pods.get("pods") or []
    if pod_items and isinstance(pod_items[0], dict):
        return pod_items[0].get("name") or "{{pod_name}}"
    return "{{pod_name}}"


def render_runbook(runbook: Runbook, context: dict) -> list[ActionStep]:
    service = context.get("service") or "unknown"
    namespace = "demo" if context.get("env", "prod") == "prod" else context.get("env", "demo")
    pods = context.get("pods") or {}
    current_replicas = int(pods.get("total") or 2)
    replacements = {
        "{{service}}": service,
        "{{namespace}}": namespace,
        "{{replicas}}": str(max(current_replicas * 2, 1)),
        "{{original_replicas}}": str(max(current_replicas, 1)),
        "{{pod_name}}": _first_pod_name(pods),
        "{{new_limit}}": "768Mi",
        "{{original_limit}}": "512Mi",
    }

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
    return rendered
