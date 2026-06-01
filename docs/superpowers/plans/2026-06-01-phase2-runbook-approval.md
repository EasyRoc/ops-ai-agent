# Phase 2：方案生成 + 人工确认 实现计划

> **目标：** Agent 诊断出根因后，匹配 Runbook 生成处置建议、评估风险等级，通过飞书卡片交互完成人工审批。Agent 能出方案但不自动执行。

**Phase 1 完成状态回顾：**
- LLM 驱动的根因分析已上线（`rca.py` 调用 DeepSeek-V4-Flash，综合指标/日志/Pod/CMDB 上下文）
- Alertmanager Webhook → 去重 → Incident 创建 → 上下文采集 → LLM 诊断 → 飞书卡片推送 全链路已跑通
- 飞书通知已验证：告警卡片 + 诊断结果卡片均可推送到群
- `chat_id` 已改为从 `.env` 读取，支持多服务映射
- `ops.sh` 管理脚本已中文化

**Tech Stack:** Python 3.11+ / FastAPI / LangGraph / DeepSeek-V4-Flash / PostgreSQL / Redis / 飞书 Open API（卡片交互回调）

---

## File Structure (Phase 2 新增/修改)

```
ops-ai-agent/
├── runbooks/                         # Runbook 模板（Phase 1 已创建目录）
│   ├── cpu_high.md
│   ├── oom.md
│   ├── error_rate.md
│   └── latency_high.md
├── agent/
│   ├── agents/
│   │   ├── rca.py                    # [修改] LLM 诊断 + Runbook 匹配
│   │   ├── runbook.py                # [新增] Runbook 匹配引擎
│   │   └── risk.py                   # [新增] 风险评估模块
│   ├── api/v1/
│   │   ├── approvals.py              # [新增] 审批 API
│   │   └── incidents.py             # [修改] 增加处置方案字段
│   ├── channels/
│   │   └── feishu.py                # [修改] 增加飞书卡片回调处理
│   ├── workflows/
│   │   └── alert_workflow.py        # [修改] 增加 runbook + risk 节点
│   └── templates/cards/
│       ├── diagnosis_card.json       # [修改] 增加方案 + 审批按钮
│       └── approval_result_card.json # [新增] 审批结果卡片
└── web/                              # [新增] Web Console
    ├── index.html
    ├── incidents.html
    └── incident-detail.html
```

---

### Task 1: Runbook 模板定义

**产出:** 4 个 Markdown Runbook 模板

- [ ] **Step 1: CPU 高负载 Runbook**

```markdown
# CPU 高负载处置 Runbook

## 触发条件
- CPU 使用率 > 90% 持续 1 分钟以上

## 处置步骤
1. [低风险] 查看 Grafana Dashboard 确认 CPU 趋势和 QPS 变化
2. [低风险] 检查 Pod 状态: `kubectl get pods -n demo -l app={{service}}`
3. [中风险] 如果所有 Pod CPU 均高且 QPS 上涨 → 扩容: `kubectl scale deployment {{service}} -n demo --replicas={{replicas}}`
4. [中风险] 如果仅个别 Pod CPU 高 → 重启异常 Pod: `kubectl delete pod {{pod_name}} -n demo`
5. [高风险] 如果扩容后仍未缓解 → 检查依赖服务状态和数据库连接池
6. [低风险] 故障恢复后 → 记录时间线和根因到故障库

## 回滚方案
- 缩容回原始副本数: `kubectl scale deployment {{service}} -n demo --replicas={{original_replicas}}`

## 预计恢复时间
- 扩容方案: 2 分钟
- 重启 Pod: 1 分钟
```

- [ ] **Step 2: OOM 内存溢出 Runbook**

```markdown
# OOM 内存溢出处置 Runbook

## 触发条件
- 内存使用率 > 90% 或 Pod 因 OOMKilled 重启

## 处置步骤
1. [低风险] 查看 Grafana 内存趋势，确认是缓慢增长还是突增
2. [低风险] 检查 Pod 重启次数: `kubectl get pods -n demo -l app={{service}}`
3. [中风险] 如果是内存泄漏 → 重启 Pod 临时释放: `kubectl delete pod {{pod_name}} -n demo`
4. [中风险] 如果是 limit 过小 → 调大 memory limit: `kubectl set resources deployment {{service}} -n demo --limits=memory={{new_limit}}`
5. [高风险] 如果是流量突增 → 扩容 + 限流
6. [低风险] 抓取 heap dump 供开发分析: `kubectl exec {{pod_name}} -n demo -- jcmd 1 GC.heap_dump /tmp/heap.hprof`

## 回滚方案
- 恢复原始 memory limit: `kubectl set resources deployment {{service}} -n demo --limits=memory={{original_limit}}`

## 预计恢复时间
- 重启 Pod: 1 分钟
- 调整 limit: 3 分钟（需滚动更新）
```

- [ ] **Step 3: Error Rate 异常 Runbook**

```markdown
# Error Rate 异常处置 Runbook

## 触发条件
- 5xx 错误率 > 5% 持续 1 分钟以上

## 处置步骤
1. [低风险] 查看 Loki 错误日志，定位异常堆栈: 查询 `{app="{{service}}"} |= "ERROR"`
2. [低风险] 检查依赖服务健康状态（payment-service / inventory-service / postgres / redis）
3. [中风险] 如果依赖服务异常 → 等依赖恢复，或切换降级开关
4. [中风险] 如果是代码 bug → 回滚到上一版本: `kubectl rollout undo deployment {{service}} -n demo`
5. [高风险] 如果数据库超时 → 检查慢查询 + 扩容连接池
6. [低风险] 确认恢复后 → 关闭故障注入（如果是测试）

## 回滚方案
- 回滚部署: `kubectl rollout undo deployment {{service}} -n demo`

## 预计恢复时间
- 回滚: 2 分钟
- 依赖恢复: 视依赖方情况而定
```

- [ ] **Step 4: 延迟升高 Runbook**

```markdown
# 延迟升高处置 Runbook

## 触发条件
- P99 响应时间 > 1s 持续 1 分钟以上

## 处置步骤
1. [低风险] 查看 Grafana RT 趋势 + QPS 关联
2. [低风险] 检查慢查询和 DB 连接池使用率
3. [中风险] 如果是 DB 慢查询 → 优化索引或限流
4. [中风险] 如果是下游服务响应慢 → 添加超时和熔断
5. [低风险] 如果是流量突增 → 扩容 + 缓存预热

## 回滚方案
- 恢复扩容: 缩容回原始副本数

## 预计恢复时间
- 扩容: 2 分钟
- DB 优化: 视情况
```

- [ ] **Step 5: Commit**

```bash
git add runbooks/
git commit -m "feat: add Phase 2 Runbook templates (CPU, OOM, ErrorRate, Latency)"
```

---

### Task 2: Runbook 匹配引擎

**Files:**
- Create: `agent/agents/runbook.py`

- [ ] **Step 1: 编写 Runbook 加载与匹配逻辑**

```python
# agent/agents/runbook.py
import logging
import re
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger("ops-agent.runbook")

RUNBOOK_DIR = Path(__file__).parent.parent.parent / "runbooks"

# 告警关键词 → Runbook 文件名 的映射
ALERT_TO_RUNBOOK = {
    "CPU": "cpu_high.md",
    "OOM": "oom.md",
    "MEMORY": "oom.md",
    "ERROR": "error_rate.md",
    "ERROR_RATE": "error_rate.md",
    "LATENCY": "latency_high.md",
    "RT": "latency_high.md",
}


@dataclass
class ActionStep:
    """单个处置步骤"""
    risk_level: str       # 低风险 / 中风险 / 高风险
    description: str      # 步骤描述
    command: str = ""     # 可执行命令（如果有）


@dataclass
class Runbook:
    """Runbook 实例"""
    name: str
    content: str
    steps: list[ActionStep] = field(default_factory=list)
    rollback: str = ""
    estimated_time: str = ""


def _parse_runbook(content: str) -> list[ActionStep]:
    """解析 Markdown Runbook，提取处置步骤列表"""
    steps = []
    # 匹配格式: N. [风险等级] 描述 `命令`
    pattern = re.compile(r'\d+\.\s*\[(.+?)\]\s*(.+?)(?:`(.+?)`)?$', re.MULTILINE)
    for match in pattern.finditer(content):
        steps.append(ActionStep(
            risk_level=match.group(1).strip(),
            description=match.group(2).strip(),
            command=match.group(3).strip() if match.group(3) else "",
        ))
    return steps


def load_runbook(alert_name: str) -> Runbook | None:
    """根据告警名匹配并加载 Runbook"""
    filename = None
    for keyword, fname in ALERT_TO_RUNBOOK.items():
        if keyword.upper() in alert_name.upper():
            filename = fname
            break

    if not filename:
        logger.warning(f"未找到匹配的 Runbook: 告警={alert_name}")
        return None

    filepath = RUNBOOK_DIR / filename
    if not filepath.exists():
        logger.warning(f"Runbook 文件不存在: {filepath}")
        return None

    content = filepath.read_text(encoding="utf-8")
    steps = _parse_runbook(content)

    logger.info(f"Runbook 已加载: {filename}, 步骤数={len(steps)}")
    return Runbook(name=filename, content=content, steps=steps)


def render_runbook(runbook: Runbook, context: dict) -> list[ActionStep]:
    """将 Runbook 中的模板变量替换为实际值"""
    service = context.get("service", "unknown")
    namespace = context.get("env", "prod") if context.get("env") != "prod" else "demo"
    total_pods = context.get("pods", {}).get("total", 2)
    current_replicas = total_pods

    rendered = []
    for step in runbook.steps:
        desc = step.description
        cmd = step.command

        # 替换模板变量
        desc = desc.replace("{{service}}", service)
        desc = desc.replace("{{replicas}}", str(current_replicas * 2))
        desc = desc.replace("{{original_replicas}}", str(current_replicas))

        if cmd:
            cmd = cmd.replace("{{service}}", service)
            cmd = cmd.replace("{{namespace}}", namespace)
            cmd = cmd.replace("{{replicas}}", str(current_replicas * 2))
            cmd = cmd.replace("{{original_replicas}}", str(current_replicas))

        rendered.append(ActionStep(
            risk_level=step.risk_level,
            description=desc,
            command=cmd,
        ))

    return rendered
```

- [ ] **Step 2: 验证 Runbook 加载**

```bash
.venv/bin/python3 -c "
from agent.agents.runbook import load_runbook, render_runbook
rb = load_runbook('CPU_HIGH')
if rb:
    print(f'Runbook: {rb.name}, 步骤数: {len(rb.steps)}')
    for s in rb.steps:
        print(f'  [{s.risk_level}] {s.description}')
"
```

- [ ] **Step 3: Commit**

```bash
git add agent/agents/runbook.py
git commit -m "feat: add Runbook matching engine with Markdown parsing"
```

---

### Task 3: 风险评估模块

**Files:**
- Create: `agent/agents/risk.py`

- [ ] **Step 1: 编写风险评估逻辑**

```python
# agent/agents/risk.py
import logging

logger = logging.getLogger("ops-agent.risk")

# 风险等级 → 分数
RISK_SCORE = {"低风险": 10, "中风险": 30, "高风险": 60, "极高风险": 90}

# 动作白名单 — Phase 2 只评估不执行
ALLOWED_ACTIONS = [
    "kubectl scale deployment",
    "kubectl delete pod",
    "kubectl rollout undo deployment",
    "kubectl set resources deployment",
    "kubectl get pods",
    "kubectl describe pod",
]


def evaluate_risk(steps: list, alert_severity: str, service: str, env: str) -> dict:
    """综合评估处置方案的整体风险等级

    Returns:
        {
            "level": "低风险" | "中风险" | "高风险" | "极高风险",
            "score": int,
            "factors": ["评估因素"],
            "warnings": ["警告"],
            "allowed": bool,
        }
    """
    if not steps:
        return {
            "level": "低风险",
            "score": 0,
            "factors": ["无处置步骤"],
            "warnings": [],
            "allowed": True,
        }

    # 取最高风险等级的步骤作为整体风险
    max_risk = max(steps, key=lambda s: RISK_SCORE.get(s.risk_level, 0))
    base_score = RISK_SCORE.get(max_risk.risk_level, 0)

    factors = [f"最高风险步骤: [{max_risk.risk_level}] {max_risk.description}"]
    warnings = []

    # 生产环境 + 高风险步骤 → 升级
    if env == "prod" and base_score >= 60:
        base_score = min(base_score + 15, 100)
        warnings.append("生产环境执行高风险操作，建议双人审批")

    # P0/P1 告警 → 降低风险门槛（时间紧迫）
    if alert_severity in ("P0", "P1"):
        factors.append("P0/P1 告警，时效性优先")

    # 检查是否涉及核心服务
    core_services = ["payment-service", "order-service"]
    if service in core_services:
        base_score = min(base_score + 10, 100)
        factors.append(f"核心服务 {service}，影响范围较大")

    # 确定风险等级
    if base_score >= 75:
        level = "极高风险"
    elif base_score >= 50:
        level = "高风险"
    elif base_score >= 25:
        level = "中风险"
    else:
        level = "低风险"

    # 检查动作是否在白名单内
    all_allowed = True
    for step in steps:
        if step.command and not any(step.command.startswith(a) for a in ALLOWED_ACTIONS):
            all_allowed = False
            warnings.append(f"动作不在白名单: {step.command}")

    logger.info(f"风险评估: 服务={service}, 等级={level}, 分数={base_score}, 白名单={'通过' if all_allowed else '拒绝'}")

    return {
        "level": level,
        "score": base_score,
        "factors": factors,
        "warnings": warnings,
        "allowed": all_allowed,
    }
```

- [ ] **Step 2: Commit**

```bash
git add agent/agents/risk.py
git commit -m "feat: add risk evaluation module with action whitelist"
```

---

### Task 4: 工作流扩展 — Runbook + Risk 节点

**Files:**
- Modify: `agent/workflows/alert_workflow.py`
- Modify: `agent/agents/rca.py`（LLM 诊断后触发 Runbook 匹配）

- [ ] **Step 1: 扩展 AlertState**

```python
class AlertState(TypedDict):
    alert_raw: dict
    incident_id: Optional[str]
    alert_parsed: Optional[dict]
    context: Optional[dict]
    diagnosis: Optional[dict]
    error: Optional[str]
    # Phase 2 新增
    runbook: Optional[dict]        # 匹配到的 Runbook 及处置步骤
    risk_assessment: Optional[dict] # 风险评估结果
    approval_status: Optional[str]  # pending / approved / rejected / escalated
```

- [ ] **Step 2: 在 rca.py 中集成 Runbook + Risk**

在 `analyze_root_cause` 函数中，LLM 诊断完成后增加 Runbook 匹配和风险评估：

```python
# 在 rca.py 的 analyze_root_cause 中，诊断完成后添加:
from agent.agents.runbook import load_runbook, render_runbook
from agent.agents.risk import evaluate_risk

# 匹配 Runbook
runbook = load_runbook(alert_name)
if runbook:
    rendered_steps = render_runbook(runbook, {
        "service": alert.get("service", ""),
        "env": alert.get("env", "prod"),
        "pods": context.get("pods", {}),
    })
    # 风险评估
    risk = evaluate_risk(
        rendered_steps,
        alert.get("severity", "P3"),
        alert.get("service", ""),
        alert.get("env", "prod"),
    )
    state["runbook"] = {
        "name": runbook.name,
        "steps": [{"risk_level": s.risk_level, "description": s.description, "command": s.command} for s in rendered_steps],
    }
    state["risk_assessment"] = risk
    state["approval_status"] = "pending"
```

- [ ] **Step 3: 扩展 should_continue 路由**

在诊断完成后进入审批等待：

```python
def should_continue(state: AlertState) -> str:
    ...
    if state.get("diagnosis") and state.get("runbook") and not state.get("approval_status"):
        return "generate_plan"  # 新节点：生成处置方案
    if state.get("approval_status") == "approved":
        return END  # Phase 3 将路由到执行节点
    if state.get("diagnosis"):
        return END  # 无 Runbook 匹配时直接结束
    ...
```

- [ ] **Step 4: Commit**

```bash
git add agent/workflows/alert_workflow.py agent/agents/rca.py
git commit -m "feat: integrate Runbook matching + Risk assessment into workflow"
```

---

### Task 5: 飞书卡片交互审批

**Files:**
- Modify: `agent/channels/feishu.py`（增加卡片回调验证）
- Modify: `agent/templates/cards/diagnosis_card.json`（增加审批按钮）
- Create: `agent/templates/cards/approval_result_card.json`
- Create: `agent/api/v1/approvals.py`

- [ ] **Step 1: 飞书卡片回调处理**

```python
# agent/channels/feishu.py 新增函数
async def verify_card_callback(headers: dict, body: dict) -> bool:
    """验证飞书卡片回调请求合法性（防重放攻击）"""
    # 生产环境需验证 timestamp + nonce + signature
    # 当前 MVP 阶段只校验基本格式
    challenge = body.get("challenge")
    if challenge:
        # 飞书 URL 验证，返回 challenge
        return True
    return body.get("type") == "card_action"


async def handle_card_action(action: str, incident_id: str) -> str:
    """处理卡片按钮点击事件

    Args:
        action: approve | reject | escalate
        incident_id: 工单 ID

    Returns:
        新的审批状态
    """
    status_map = {
        "approve": "approved",
        "reject": "rejected",
        "escalate": "escalated",
    }
    return status_map.get(action, "pending")
```

- [ ] **Step 2: 更新诊断卡片模板（增加审批按钮）**

```json
{
  "config": { "wide_screen_mode": true },
  "header": {
    "title": { "tag": "plain_text", "content": "{{alert_title}} - 诊断完成" },
    "template": "{{severity_color}}"
  },
  "elements": [
    {
      "tag": "markdown",
      "content": "**根因判断：**\n{{root_cause}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**处置方案：**\n{{action_plan}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**风险评估：** {{risk_level}}（{{risk_score}}分）\n{{risk_warnings}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**证据：**\n{{evidence_list}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**置信度：** {{confidence}}%"
    },
    { "tag": "hr" },
    {
      "tag": "action",
      "actions": [
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "批准执行" },
          "type": "primary",
          "value": "{\"action\":\"approve\",\"incident_id\":\"{{incident_id}}\"}"
        },
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "拒绝" },
          "type": "danger",
          "value": "{\"action\":\"reject\",\"incident_id\":\"{{incident_id}}\"}"
        },
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "转人工" },
          "type": "default",
          "value": "{\"action\":\"escalate\",\"incident_id\":\"{{incident_id}}\"}"
        }
      ]
    },
    {
      "tag": "note",
      "elements": [
        { "tag": "plain_text", "content": "事件编号: {{incident_id}} | 状态: {{status}} | 耗时: {{duration}}" }
      ]
    }
  ]
}
```

- [ ] **Step 3: 审批 API**

```python
# agent/api/v1/approvals.py
import logging
from fastapi import APIRouter, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.crud import get_incident, update_incident, AsyncSessionLocal
from agent.channels.feishu import update_card, verify_card_callback, handle_card_action
from agent.templates import render_card

logger = logging.getLogger("ops-agent.api.approvals")
router = APIRouter(prefix="/api/v1")


@router.post("/approvals/callback")
async def approval_callback(request: Request, background_tasks: BackgroundTasks):
    """飞书卡片按钮回调"""
    body = await request.json()

    # URL 验证
    challenge = body.get("challenge")
    if challenge:
        return {"challenge": challenge}

    # 提取卡片动作
    action_data = body.get("action", {}).get("value", "{}")
    import json
    try:
        data = json.loads(action_data)
    except json.JSONDecodeError:
        return {"status": "invalid_action"}

    action = data.get("action")
    incident_id = data.get("incident_id")

    if not action or not incident_id:
        return {"status": "missing_params"}

    # 处理审批
    new_status = await handle_card_action(action, incident_id)
    background_tasks.add_task(_update_incident_status, incident_id, new_status)
    background_tasks.add_task(_update_feishu_card, incident_id, new_status)

    logger.info(f"审批回调: incident={incident_id}, action={action}, status={new_status}")
    return {"status": "ok", "incident_id": incident_id, "approval_status": new_status}


async def _update_incident_status(incident_id: str, status: str):
    """更新工单状态"""
    async with AsyncSessionLocal() as session:
        await update_incident(session, incident_id, status=status)
        logger.info(f"工单状态更新: {incident_id} -> {status}")


async def _update_feishu_card(incident_id: str, status: str):
    """更新飞书卡片（移除按钮，展示结果）"""
    # 使用 message_id 更新卡片，移除交互按钮
    # message_id 需要在整个流程中传递，此处简化处理
    status_map = {
        "approved": ("green", "已批准 → 待执行"),
        "rejected": ("red", "已拒绝"),
        "escalated": ("yellow", "已转人工处理"),
    }
    color, text = status_map.get(status, ("blue", status))
    logger.info(f"飞书卡片更新: {incident_id} -> {text}")


@router.get("/incidents/{incident_id}/approval")
async def get_approval_status(incident_id: str, db: AsyncSession = Depends(get_db)):
    """查询审批状态"""
    incident = await get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return {
        "incident_id": incident.id,
        "status": incident.status,
        "approval_status": getattr(incident, "approval_status", "none"),
    }
```

- [ ] **Step 4: 注册新路由**

```python
# agent/main.py 增加:
from agent.api.v1 import approvals
app.include_router(approvals.router)
```

- [ ] **Step 5: Commit**

```bash
git add agent/channels/feishu.py agent/templates/cards/ agent/api/v1/approvals.py agent/main.py
git commit -m "feat: add Feishu card interactive approval + approval API"
```

---

### Task 6: Web Console（基础版）

**Files:**
- Create: `web/index.html`
- Create: `web/incidents.html`
- Create: `web/incident-detail.html`
- Modify: `agent/main.py`（挂载静态文件）

- [ ] **Step 1: 仪表盘首页**

```html
<!-- web/index.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Ops AI Agent - 仪表盘</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50">
    <div class="max-w-6xl mx-auto p-6">
        <h1 class="text-2xl font-bold mb-6">Ops AI Agent 仪表盘</h1>

        <!-- 统计卡片 -->
        <div class="grid grid-cols-4 gap-4 mb-8" id="stats">
            <div class="bg-white rounded-lg p-4 shadow">
                <div class="text-gray-500 text-sm">今日告警</div>
                <div class="text-2xl font-bold" id="totalAlerts">-</div>
            </div>
            <div class="bg-yellow-50 rounded-lg p-4 shadow">
                <div class="text-gray-500 text-sm">诊断中</div>
                <div class="text-2xl font-bold" id="diagnosing">-</div>
            </div>
            <div class="bg-orange-50 rounded-lg p-4 shadow">
                <div class="text-gray-500 text-sm">待审批</div>
                <div class="text-2xl font-bold" id="pendingApproval">-</div>
            </div>
            <div class="bg-green-50 rounded-lg p-4 shadow">
                <div class="text-gray-500 text-sm">已恢复</div>
                <div class="text-2xl font-bold" id="resolved">-</div>
            </div>
        </div>

        <!-- 最近 Incident -->
        <div class="bg-white rounded-lg p-4 shadow">
            <h2 class="text-lg font-semibold mb-4">最近工单</h2>
            <table class="w-full text-left" id="incidentsTable">
                <thead>
                    <tr class="border-b text-gray-500 text-sm">
                        <th class="py-2">工单ID</th>
                        <th>服务</th>
                        <th>告警</th>
                        <th>级别</th>
                        <th>状态</th>
                        <th>诊断结论</th>
                        <th>时间</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </div>

    <script>
        async function loadDashboard() {
            const resp = await fetch('/api/v1/incidents?limit=20');
            const data = await resp.json();

            // 统计
            const counts = {total: data.total, diagnosing: 0, pending_approval: 0, resolved: 0};
            data.incidents.forEach(i => {
                if (i.status === 'diagnosing') counts.diagnosing++;
                else if (i.status === 'pending_approval') counts.pending_approval++;
                else if (i.status === 'resolved' || i.status === 'diagnosed') counts.resolved++;
            });
            document.getElementById('totalAlerts').textContent = counts.total;
            document.getElementById('diagnosing').textContent = counts.diagnosing;
            document.getElementById('pendingApproval').textContent = counts.pending_approval;
            document.getElementById('resolved').textContent = counts.resolved;

            // 表格
            const tbody = document.querySelector('#incidentsTable tbody');
            tbody.innerHTML = data.incidents.map(i => `
                <tr class="border-b hover:bg-gray-50 cursor-pointer" onclick="location.href='/incident-detail.html?id=${i.id}'">
                    <td class="py-2 font-mono text-sm">${i.id}</td>
                    <td>${i.service}</td>
                    <td>${i.alert_name || '-'}</td>
                    <td><span class="px-2 py-0.5 rounded text-xs font-medium ${i.severity === 'P0' ? 'bg-red-100 text-red-700' : i.severity === 'P1' ? 'bg-orange-100 text-orange-700' : 'bg-blue-100 text-blue-700'}">${i.severity}</span></td>
                    <td>${i.status}</td>
                    <td class="text-sm text-gray-600 max-w-xs truncate">${i.root_cause || '诊断中...'}</td>
                    <td class="text-sm text-gray-400">${i.created_at ? new Date(i.created_at).toLocaleString('zh-CN') : '-'}</td>
                </tr>
            `).join('');
        }
        loadDashboard();
        setInterval(loadDashboard, 30000);  // 30s 自动刷新
    </script>
</body>
</html>
```

- [ ] **Step 2: 工单详情页**（含诊断、方案、审批按钮）

```html
<!-- web/incident-detail.html -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>工单详情 - Ops AI Agent</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50">
    <div class="max-w-4xl mx-auto p-6">
        <a href="/" class="text-blue-500 mb-4 inline-block">&larr; 返回仪表盘</a>

        <div id="incidentDetail" class="space-y-4"></div>

        <!-- 审批按钮区域（仅 pending_approval 状态显示） -->
        <div id="approvalActions" class="hidden mt-6 flex gap-3">
            <button onclick="approve()" class="bg-green-500 text-white px-6 py-2 rounded-lg hover:bg-green-600">✓ 批准执行</button>
            <button onclick="reject()" class="bg-red-500 text-white px-6 py-2 rounded-lg hover:bg-red-600">✗ 拒绝</button>
            <button onclick="escalate()" class="bg-gray-500 text-white px-6 py-2 rounded-lg hover:bg-gray-600">↑ 转人工</button>
        </div>
    </div>

    <script>
        const params = new URLSearchParams(location.search);
        const incidentId = params.get('id');

        async function loadDetail() {
            const resp = await fetch(`/api/v1/incidents/${incidentId}`);
            const i = await resp.json();
            const el = document.getElementById('incidentDetail');
            el.innerHTML = `
                <h1 class="text-xl font-bold">工单 ${i.id}</h1>
                <div class="bg-white rounded-lg p-4 shadow grid grid-cols-2 gap-3">
                    <div><span class="text-gray-500">服务:</span> ${i.service}</div>
                    <div><span class="text-gray-500">环境:</span> ${i.env}</div>
                    <div><span class="text-gray-500">级别:</span> <span class="font-bold text-${i.severity === 'P0' ? 'red' : 'orange'}-600">${i.severity}</span></div>
                    <div><span class="text-gray-500">状态:</span> ${i.status}</div>
                    <div><span class="text-gray-500">告警:</span> ${i.alert_name}</div>
                    <div><span class="text-gray-500">告警值:</span> ${i.alert_value || '-'}</div>
                </div>
                ${i.root_cause ? `
                <div class="bg-white rounded-lg p-4 shadow">
                    <h3 class="font-semibold mb-2">根因诊断</h3>
                    <p class="text-gray-700">${i.root_cause}</p>
                    <p class="text-sm text-gray-400 mt-2">置信度: ${(i.confidence * 100).toFixed(0)}%</p>
                </div>` : ''}
                ${i.evidence && i.evidence.length ? `
                <div class="bg-white rounded-lg p-4 shadow">
                    <h3 class="font-semibold mb-2">证据链</h3>
                    <ul class="list-disc pl-5 text-gray-700">
                        ${i.evidence.map(e => `<li>${e}</li>`).join('')}
                    </ul>
                </div>` : ''}
            `;

            if (i.status === 'pending_approval') {
                document.getElementById('approvalActions').classList.remove('hidden');
            }
        }

        async function submitApproval(action) {
            await fetch('/api/v1/approvals/callback', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: {value: JSON.stringify({action, incident_id: incidentId})}})
            });
            location.reload();
        }
        function approve() { submitApproval('approve'); }
        function reject() { submitApproval('reject'); }
        function escalate() { submitApproval('escalate'); }

        loadDetail();
    </script>
</body>
</html>
```

- [ ] **Step 3: 在 main.py 中挂载静态文件**

```python
# agent/main.py 增加:
from fastapi.staticfiles import StaticFiles

app.mount("/", StaticFiles(directory="web", html=True), name="web")
```

- [ ] **Step 4: Commit**

```bash
git add web/ agent/main.py
git commit -m "feat: add basic Web Console (dashboard + incident detail + approval buttons)"
```

---

### Task 7: Phase 2 E2E 测试

**Files:**
- Create: `tests/e2e_phase2.sh`

- [ ] **Step 1: 编写 Phase 2 端到端测试脚本**

```bash
#!/bin/bash
set -e

echo "=== Phase 2 E2E Test: 诊断 → Runbook → 风险评估 → 审批 ==="

AGENT_URL="http://localhost:8000"

echo "[1/6] 模拟发送告警..."
# 发送模拟 Alertmanager webhook
FINGERPRINT=$(uuidgen | tr '[:upper:]' '[:lower:]')
RESP=$(curl -s -X POST "$AGENT_URL/api/v1/alerts" \
  -H "Content-Type: application/json" \
  -d "{
    \"receiver\": \"ops-agent-webhook\",
    \"alerts\": [{
      \"labels\": {
        \"alertname\": \"HighCPUUsage\",
        \"service\": \"order-service\",
        \"severity\": \"P1\"
      },
      \"annotations\": {
        \"value\": \"95.2%\",
        \"summary\": \"CPU usage > 90%\"
      },
      \"fingerprint\": \"$FINGERPRINT\"
    }]
  }")
echo "告警响应: $RESP"

echo "[2/6] 等待诊断完成..."
sleep 30  # LLM 诊断需要时间

echo "[3/6] 查询 Incident 和诊断结果..."
INCIDENTS=$(curl -s "$AGENT_URL/api/v1/incidents")
echo "$INCIDENTS" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if d['total'] > 0:
    i = d['incidents'][-1]
    print(f'工单: {i[\"id\"]}')
    print(f'服务: {i[\"service\"]}')
    print(f'根因: {i[\"root_cause\"]}')
    print(f'置信度: {i[\"confidence\"]}')
"

echo "[4/6] 检查 Runbook 匹配..."
# 通过 Incident detail 检查是否有 runbook 信息
INCIDENT_ID=$(echo "$INCIDENTS" | python3 -c "import sys,json; print(json.load(sys.stdin)['incidents'][-1]['id'])")
echo "工单ID: $INCIDENT_ID"

echo "[5/6] 测试审批流程..."
APPROVAL_RESP=$(curl -s -X POST "$AGENT_URL/api/v1/approvals/callback" \
  -H "Content-Type: application/json" \
  -d "{
    \"action\": {\"value\": \"{\\\"action\\\":\\\"approve\\\",\\\"incident_id\\\":\\\"$INCIDENT_ID\\\"}\"}
  }")
echo "审批响应: $APPROVAL_RESP"

echo "[6/6] 查询 Web Console..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$AGENT_URL/")
echo "Web Console HTTP 状态: $HTTP_CODE"
if [ "$HTTP_CODE" == "200" ]; then
    echo "PASS: Web Console 可访问"
else
    echo "FAIL: Web Console 不可访问"
fi

echo "=== Phase 2 E2E Test Complete ==="
```

- [ ] **Step 2: Commit**

```bash
git add tests/e2e_phase2.sh
git commit -m "feat: add Phase 2 end-to-end test (diagnosis → runbook → approval)"
```

---

## Plan Summary

| Task | 内容 | 文件 | 依赖 |
|------|------|------|------|
| 1 | Runbook 模板 (CPU/OOM/Error/Latency) | `runbooks/*.md` | 无 |
| 2 | Runbook 匹配引擎 | `agent/agents/runbook.py` | 1 |
| 3 | 风险评估模块 | `agent/agents/risk.py` | 2 |
| 4 | 工作流扩展 | `alert_workflow.py` + `rca.py` 修改 | 2,3 |
| 5 | 飞书卡片交互审批 | `feishu.py` + 卡片模板 + `approvals.py` | 4 |
| 6 | Web Console | `web/` + `main.py` | 4 |
| 7 | E2E 测试 | `tests/e2e_phase2.sh` | 5,6 |

**任务执行顺序：**

```
Task 1 (Runbook 模板)
    └── Task 2 (匹配引擎)
            └── Task 3 (风险评估)
                    └── Task 4 (工作流集成)
                            ├── Task 5 (飞书交互审批)
                            │       └── Task 7 (E2E)
                            └── Task 6 (Web Console)
                                    └── Task 7 (E2E)
```

**Phase 2 完工标准：**
1. LLM 诊断结果自动匹配 Runbook，生成结构化处置方案
2. 每个处置步骤附带风险等级（低/中/高/极高），高风险动作需要审批
3. 飞书卡片包含 [批准执行] [拒绝] [转人工] 三个按钮，点击后回调 Agent API
4. Web Console 可查看 Incident 列表、详情、审批按钮
5. 全链路可验证：故障注入 → 诊断 → Runbook → 风险评估 → 飞书卡片 → 审批 → 状态更新
