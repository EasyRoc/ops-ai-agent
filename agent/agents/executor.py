import asyncio
import logging
import shlex
import time
from datetime import datetime, timezone

from agent.agents.audit import write_audit
from agent.db.crud import AsyncSessionLocal, create_execution, update_incident
from agent.db.models import Execution

logger = logging.getLogger("ops-agent.executor")

# Phase 3 的自动执行只接受明确白名单命令。
# 注意：这里做“前缀匹配”是为了兼容 namespace、replicas 等参数，但前缀本身必须足够具体。
ALLOWED_COMMANDS = {
    "kubectl scale deployment": {"risk": "medium", "reversible": True},
    "kubectl delete pod": {"risk": "medium", "reversible": False},
    "kubectl rollout undo": {"risk": "high", "reversible": False},
    "kubectl set resources": {"risk": "medium", "reversible": True},
    "kubectl get pods": {"risk": "low", "reversible": True},
    "kubectl describe pod": {"risk": "low", "reversible": True},
}

# 读操作可以展示在 Runbook 里，但自动执行阶段不需要写入 executions。
# 自动化真正要做的是“处置动作”，读操作留给诊断卡片和人工复核。
READ_ONLY_PREFIXES = (
    "kubectl get pods",
    "kubectl describe pod",
)


def validate_command(command: str) -> tuple[bool, str]:
    """校验命令是否命中白名单，返回 (是否允许, 风险/原因)。"""
    normalized = (command or "").strip()
    logger.info("进入 validate_command: command=%s", normalized or "-")
    for allowed_prefix, meta in ALLOWED_COMMANDS.items():
        if normalized.startswith(allowed_prefix):
            logger.info(
                "命令白名单校验通过: prefix=%s, risk=%s",
                allowed_prefix,
                meta["risk"],
            )
            return True, meta["risk"]
    reason = f"命令不在白名单: {normalized}"
    logger.warning("命令白名单校验失败: %s", reason)
    return False, reason


def is_read_only_command(command: str) -> bool:
    """判断 Runbook 命令是否只是查询类动作。"""
    normalized = (command or "").strip()
    return any(normalized.startswith(prefix) for prefix in READ_ONLY_PREFIXES)


def select_executable_steps(action_plan: list[dict]) -> list[dict]:
    """挑选本次真正自动执行的步骤。

    设计上只执行第一个“会改变系统状态”的白名单步骤：
    - 避免一个 Runbook 同时扩容、删 Pod、回滚时被一次审批全部串行执行；
    - 后续动作应基于验证结果或人工二次审批继续推进；
    - 查询类步骤不执行，防止 executions 里充满无副作用的噪音记录。
    """
    logger.info("进入 select_executable_steps: steps=%s", len(action_plan or []))
    for step in action_plan or []:
        command = (step.get("command") or "").strip()
        if not command:
            logger.info("跳过无命令步骤: description=%s", step.get("description", "-"))
            continue

        allowed, reason = validate_command(command)
        if not allowed:
            logger.warning("跳过非白名单步骤: reason=%s", reason)
            continue
        if is_read_only_command(command):
            logger.info("跳过只读步骤: command=%s", command)
            continue

        logger.info("选中自动执行步骤: command=%s", command)
        return [step]

    logger.info("没有可自动执行的变更步骤")
    return []


async def execute_kubectl(command: str, timeout: int = 60) -> dict:
    """安全执行 kubectl 命令并返回标准化结果。"""
    logger.info("进入 execute_kubectl: command=%s, timeout=%ss", command, timeout)
    allowed, reason = validate_command(command)
    if not allowed:
        logger.warning("kubectl 执行被拦截: reason=%s", reason)
        return {
            "status": "blocked",
            "exit_code": None,
            "stdout": "",
            "stderr": reason,
            "duration": 0,
        }

    started = time.monotonic()
    process = None
    try:
        # shlex.split 避免 shell=True 带来的命令注入风险，参数由 kubectl 自己解析。
        process = await asyncio.create_subprocess_exec(
            *shlex.split(command),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        duration = round(time.monotonic() - started, 3)
        result = {
            "status": "success" if process.returncode == 0 else "failed",
            "exit_code": process.returncode,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
            "duration": duration,
        }
        logger.info(
            "kubectl 执行完成: command=%s, exit_code=%s, duration=%.3fs",
            command,
            process.returncode,
            duration,
        )
        if process.returncode != 0:
            logger.warning("kubectl 返回非零退出码: stderr=%s", result["stderr"][:500])
        return result
    except asyncio.TimeoutError:
        if process:
            process.kill()
            await process.communicate()
        duration = round(time.monotonic() - started, 3)
        logger.error("kubectl 执行超时: command=%s, duration=%.3fs", command, duration)
        return {
            "status": "timeout",
            "exit_code": None,
            "stdout": "",
            "stderr": f"command timeout after {timeout}s",
            "duration": duration,
        }
    except Exception as exc:
        duration = round(time.monotonic() - started, 3)
        logger.error("kubectl 执行异常: command=%s, error=%s", command, exc, exc_info=True)
        return {
            "status": "failed",
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "duration": duration,
        }


async def record_execution(
    incident_id: str,
    action: str,
    operator: str,
    status: str,
    result: dict,
    round_num: int = 1,
) -> Execution:
    """将一次真实执行落库，供 Web Console 和报告回溯。"""
    logger.info(
        "进入 record_execution: incident=%s, operator=%s, status=%s, round=%s, action=%s",
        incident_id,
        operator,
        status,
        round_num,
        action,
    )
    async with AsyncSessionLocal() as session:
        execution = Execution(
            incident_id=incident_id,
            action=action,
            operator=operator,
            status=status,
            result=result,
            round=round_num,
            completed_at=datetime.now(timezone.utc),
        )
        saved = await create_execution(session, execution)
    logger.info("执行记录已保存: incident=%s, execution_id=%s, round=%s", incident_id, saved.id, round_num)
    return saved


async def update_incident_status(incident_id: str, status: str, **extra_fields) -> None:
    """更新 Incident 状态；执行链路里多处需要用到，集中封装便于日志一致。"""
    logger.info(
        "进入 executor.update_incident_status: incident=%s, status=%s, extra=%s",
        incident_id,
        status,
        list(extra_fields.keys()),
    )
    try:
        async with AsyncSessionLocal() as session:
            await update_incident(session, incident_id, status=status, **extra_fields)
        logger.info("执行链路状态已更新: incident=%s, status=%s", incident_id, status)
    except Exception as exc:
        # 自动执行节点的核心结果已经在 state/executions 中体现；状态同步失败要暴露日志，
        # 但不能让一个短暂的数据库问题把整个工作流打断成未知状态。
        logger.error(
            "执行链路状态更新失败: incident=%s, status=%s, error=%s",
            incident_id,
            status,
            exc,
            exc_info=True,
        )


async def execute_approved_plan(state: dict) -> dict:
    """执行审批通过后的 Runbook 处置动作。"""
    incident_id = state.get("incident_id") or ""
    operator = state.get("operator") or "system"
    runbook = state.get("runbook") or {}
    steps = runbook.get("steps") or []
    risk_assessment = state.get("risk_assessment") or {}
    try:
        round_num = max(1, int(state.get("retry_count") or 1))
    except (TypeError, ValueError):
        round_num = 1
    logger.info(
        "进入 execute_approved_plan: incident=%s, operator=%s, runbook=%s, steps=%s, risk_allowed=%s, round=%s",
        incident_id,
        operator,
        runbook.get("name", "-"),
        len(steps),
        risk_assessment.get("allowed"),
        round_num,
    )

    if not risk_assessment.get("allowed", False):
        reason = "风险评估不允许自动执行"
        state["approval_status"] = "escalated"
        state["execution_result"] = {
            "status": "blocked",
            "reason": reason,
            "executed": 0,
            "results": [],
        }
        await write_audit(
            incident_id,
            operator,
            "execution_blocked",
            {"reason": reason, "risk_assessment": risk_assessment},
        )
        await update_incident_status(
            incident_id,
            "escalated",
            approval_status="escalated",
        )
        logger.warning("自动执行被阻断: incident=%s, reason=%s", incident_id, reason)
        return state

    selected_steps = select_executable_steps(steps)
    if not selected_steps:
        reason = "没有可自动执行的变更步骤"
        state["approval_status"] = "escalated"
        state["execution_result"] = {
            "status": "blocked",
            "reason": reason,
            "executed": 0,
            "results": [],
        }
        await write_audit(incident_id, operator, "execution_blocked", {"reason": reason})
        await update_incident_status(incident_id, "escalated", approval_status="escalated")
        logger.warning("自动执行没有可执行步骤: incident=%s", incident_id)
        return state

    results = []
    await update_incident_status(incident_id, "executing", approval_status="approved")
    for step in selected_steps:
        command = step.get("command", "")
        logger.info("准备执行 Runbook 步骤: incident=%s, command=%s", incident_id, command)
        result = await execute_kubectl(command)
        status = "success" if result.get("exit_code") == 0 else result.get("status", "failed")
        await record_execution(incident_id, command, operator, status, result, round_num=round_num)
        await write_audit(
            incident_id,
            operator,
            "command_executed",
            {"command": command, "status": status, "result": result},
        )
        results.append({"step": step, "result": result, "status": status})
        if status != "success":
            logger.warning("自动执行步骤失败，停止后续动作: incident=%s, command=%s", incident_id, command)
            break

    success = bool(results) and all(item["status"] == "success" for item in results)
    final_status = "success" if success else "failed"
    state["execution_result"] = {
        "status": final_status,
        "executed": len(results),
        "results": results,
    }
    await update_incident_status(
        incident_id,
        "executed" if success else "execution_failed",
        approval_status="approved" if success else "escalated",
    )
    if not success:
        state["approval_status"] = "escalated"
    logger.info(
        "自动执行完成: incident=%s, status=%s, executed=%s",
        incident_id,
        final_status,
        len(results),
    )
    return state


async def execute(state: dict) -> dict:
    """LangGraph 节点入口：审批通过后进入自动执行。"""
    logger.info("进入 execute 节点: incident=%s", state.get("incident_id", "-"))
    return await execute_approved_plan(state)


async def create_gitops_pr(incident_id: str, changes: dict) -> dict:
    """Mock GitOps 执行器，给后续扩展保留稳定接口。"""
    logger.info("进入 create_gitops_pr(mock): incident=%s", incident_id)
    return {
        "status": "mock",
        "pr_url": f"https://github.com/EasyRoc/ops-config/pull/mock-{incident_id}",
        "changes": changes,
    }
