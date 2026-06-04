# Phase 3：自动执行 + 验证 + 沉淀 实施方案

> 基于 [开发任务清单](../specs/2026-05-31-ops-agent-dev-tasklist.md) Phase 3 拆解，结合当前代码现状制定。

## 一、当前状态盘点

### 已就绪的基础设施

| 能力 | 现状 | 相关文件 |
|------|------|----------|
| `executions` 表 | ORM 模型已定义，CRUD 未实现 | `db/models.py:35` |
| `reports` 表 | ORM 模型已定义，CRUD 未实现 | `db/models.py:48` |
| `audit_logs` 表 | ORM 模型已定义，`create_audit_log` 已实现 | `db/models.py:57`、`db/crud.py:75` |
| K8S 工具函数 | `get_service_pods` 已实现，无写操作 | `tools/kubernetes.py` |
| 审批状态机 | `pending → approved/rejected/escalated` 已跑通 | `api/v1/approvals.py` |
| 飞书卡片更新 | `update_card` 已实现 | `channels/feishu.py:109` |
| 工作流引擎 | LangGraph 状态图可扩展新节点 | `workflows/alert_workflow.py` |
| Web Console | 静态文件挂载，可新增页面 | `main.py:46` |

### Phase 3 需要从零构建的模块

- **Executor Agent** — 白名单命令的实际执行
- **Verify Agent** — 执行后指标验证
- **Report Agent** — 故障报告自动生成
- **审计日志集成** — 在现有 CRUD 基础上全链路埋点
- **RBAC 中间件** — 角色鉴权
- **历史故障库** — 结构化案例沉淀

---

## 二、总体架构

```
Phase 2 结束状态:  diagnose → (End，等待审批)
Phase 3 新增节点:  diagnose → execute → verify → report → (End)
                            ↑ 审批通过后触发   │
                                             ↓ 失败则 escalate
                                        verify 超时 → escalate
```

工作流扩展后的状态定义：

```python
class AlertState(TypedDict):
    # Phase 1 字段（不变）
    alert_raw: dict
    incident_id: Optional[str]
    alert_parsed: Optional[dict]
    context: Optional[dict]
    diagnosis: Optional[dict]
    # Phase 2 字段（不变）
    runbook: Optional[dict]
    risk_assessment: Optional[dict]
    approval_status: Optional[str]
    # Phase 3 新增
    execution_result: Optional[dict]    # execute 节点输出
    verification_result: Optional[dict] # verify 节点输出
    report: Optional[dict]              # report 节点输出
    error: Optional[str]
```

工作流节点变更：

```
现有: parse_alert → collect_context → diagnose → END
Phase 3:
  parse_alert → collect_context → diagnose ─┬─ approval=rejected → END
                                            └─ approval=approved → execute → verify ─┬─ 恢复 → report → END
                                                                                       └─ 超时/失败 → escalate → END
```

> **关键设计决策**：execute/verify/report 不走审批回调的 background_tasks，而是在审批回调中触发工作流后续节点，保持状态流转在 LangGraph 内闭环。

---

## 三、分模块实施方案

### 3.1 Executor Agent

**文件**：`agent/agents/executor.py`

#### 3.1.1 命令白名单与安全检查

```python
# 白名单命令前缀（Phase 2 的 ALLOWED_ACTIONS 移到这里统一管理）
ALLOWED_COMMANDS = {
    "kubectl scale deployment":  {"risk": "medium", "reversible": True},
    "kubectl delete pod":        {"risk": "medium", "reversible": False},
    "kubectl rollout undo":      {"risk": "high",   "reversible": False},
    "kubectl set resources":     {"risk": "medium", "reversible": True},
    "kubectl get pods":          {"risk": "low",    "reversible": True},
    "kubectl describe pod":      {"risk": "low",    "reversible": True},
}

def validate_command(command: str) -> tuple[bool, str]:
    """校验命令是否在白名单内，返回 (是否允许, 原因)"""
    for allowed, meta in ALLOWED_COMMANDS.items():
        if command.strip().startswith(allowed):
            return True, meta["risk"]
    return False, f"命令不在白名单: {command}"
```

#### 3.1.2 K8S 命令执行

使用 `asyncio.create_subprocess_exec` 执行 kubectl 命令，捕获 stdout/stderr 和退出码：

```python
async def execute_kubectl(command: str, timeout: int = 60) -> dict:
    """安全执行 kubectl 命令，返回 {exit_code, stdout, stderr, duration}"""
```

- 每次执行前校验白名单
- 超时 60 秒自动 kill
- 执行结果写入 `executions` 表

#### 3.1.3 执行状态追踪

`executions` 表已有字段，直接写入：

```python
# 状态流转: pending → running → success/failed
async def _record_execution(incident_id, action, operator, status, result):
    async with AsyncSessionLocal() as session:
        exec = Execution(
            incident_id=incident_id,
            action=action,
            operator=operator,
            status=status,
            result=result,
        )
        session.add(exec)
        await session.commit()
```

#### 3.1.4 审批回调触发执行

修改 `agent/api/v1/approvals.py` 的 `approval_callback`：审批通过后，不再仅更新状态，而是触发工作流的 execute 节点：

```python
# approval_callback 中，action == "approve" 且 risk 可自动执行时
if approval_status == "approved" and risk_allowed:
    background_tasks.add_task(run_execution_workflow, incident_id, body)
```

新增 `run_execution_workflow` 函数：从 DB 恢复 AlertState，从 `diagnose` 之后继续执行 `execute → verify → report`。

#### 3.1.5 GitOps 执行器（Mock）

```python
async def create_gitops_pr(incident_id: str, changes: dict) -> dict:
    """模拟 GitOps 流程：输出 PR 链接和变更摘要"""
    return {
        "status": "mock",
        "pr_url": f"https://github.com/EasyRoc/ops-config/pull/mock-{incident_id}",
        "changes": changes,
    }
```

### 3.2 Verify Agent

**文件**：`agent/agents/verify.py`

#### 3.2.1 验证逻辑

执行完成后，轮询 Prometheus 指标判断是否恢复：

```python
async def verify_recovery(incident_id: str, context: dict, 
                          alert_name: str, max_wait: int = 300) -> dict:
    """轮询验证，最长等待 max_wait 秒"""
    # 根据告警类型确定验证条件
    thresholds = {
        "CPU":     ("cpu", "current", lambda v: v < 70),     # CPU < 70%
        "OOM":     ("memory", "current", lambda v: v < 0.85), # Mem < 85%
        "ERROR":   ("error_rate", "current", lambda v: v < 0.02), # Error < 2%
        "LATENCY": ("rt_avg", "current", lambda v: v < 1.0),  # P99 < 1s
    }
```

- 每 15 秒查询一次指标
- 任一指标满足阈值则认为恢复
- 最长等待 5 分钟，超时则升级人工

#### 3.2.2 验证工作流节点

```python
async def verify(state: AlertState) -> AlertState:
    """Node: verify recovery after execution"""
    result = await verify_recovery(
        state["incident_id"],
        state["context"],
        state.get("alert_parsed", {}).get("alertname", ""),
    )
    state["verification_result"] = result
    if not result.get("recovered"):
        state["approval_status"] = "escalated"  # 超时升级
    return state
```

### 3.3 Report Agent

**文件**：`agent/agents/report.py`

#### 3.3.1 报告结构

```python
@dataclass
class IncidentReport:
    incident_id: str
    title: str
    timeline: list[dict]       # 事件时间线
    root_cause: str
    confidence: float
    evidence: list[str]
    action_plan: list[dict]    # 执行的处置步骤
    execution_result: dict     # 执行结果
    verification: dict         # 验证结果
    suggestions: list[str]     # 后续改进建议
    created_at: str
```

#### 3.3.2 LLM 摘要生成

从 Incident 全量数据生成 Markdown 报告，复用现有 `llm/client.py` 的 `chat_json` 接口：

```
Prompt: 根据以下 Incident 数据生成故障报告摘要，包含：
1. 一句话总结
2. 影响范围
3. 后续改进建议（3条）
```

#### 3.3.3 Report API

```python
# agent/api/v1/reports.py
@router.get("/reports/{incident_id}")
async def get_report(incident_id: str):
    """返回 Incident Report，支持 ?format=json|markdown"""

@router.get("/reports")
async def list_reports(limit: int = 20):
    """列出所有报告"""
```

### 3.4 审计日志全链路埋点

**文件**：`agent/agents/audit.py`（审计工具函数，分散在各节点调用）

#### 3.4.1 埋点位置

| 节点 | 审计事件 | actor | action |
|------|----------|-------|--------|
| parse_alert | 告警接收 | system | alert_received |
| diagnose | LLM 诊断完成 | llm | diagnosis_completed |
| approval_callback | 审批操作 | operator_id | approved/rejected/escalated |
| execute | 命令执行 | operator_id | command_executed |
| verify | 验证结果 | system | recovery_verified/failed |
| report | 报告生成 | system | report_generated |

#### 3.4.2 实现

```python
async def write_audit(incident_id: str, actor: str, action: str, detail: dict = None):
    async with AsyncSessionLocal() as session:
        await create_audit_log(session, AuditLog(
            incident_id=incident_id,
            actor=actor,
            action=action,
            detail=detail or {},
        ))
```

现有 `db/crud.py:75` 的 `create_audit_log` 已可用，直接在各节点中调用即可。

### 3.5 RBAC 中间件

**文件**：`agent/middleware/auth.py`

#### 3.5.1 角色定义

| 角色 | 权限 |
|------|------|
| viewer | GET 只读接口 |
| operator | viewer + 审批操作 + 执行白名单命令 |
| admin | operator + Runbook 管理 + 白名单配置 |

#### 3.5.2 实现方案

最小可用版本：HTTP Header 传 `X-User-Role`，FastAPI 中间件校验：

```python
# middleware/auth.py
from fastapi import Request, HTTPException

ROLE_PERMISSIONS = {
    "viewer":   ["GET"],
    "operator": ["GET", "POST_APPROVAL", "POST_EXECUTE"],
    "admin":    ["*"],
}

async def rbac_middleware(request: Request, call_next):
    role = request.headers.get("X-User-Role", "viewer")
    # 校验角色是否有权限访问该接口
    ...
```

> **注意**：Phase 3 RBAC 做最小可用版本（Header 传角色），不做完整登录认证。生产环境可替换为 OAuth2/JWT。

### 3.6 历史故障库

**文件**：`agent/agents/fault_db.py`

#### 3.6.1 数据模型

- 不需要新表，复用 `reports` 表
- 新增 `fault_patterns` 字段（从 incidents 中提取的告警特征指纹）

#### 3.6.2 相似故障检索

```python
async def find_similar_incidents(alert_name: str, service: str, limit: int = 5):
    """按告警类型+服务查找历史相似故障"""
```

#### 3.6.3 Runbook 效果评分

```python
async def score_runbook(runbook_name: str):
    """根据执行结果（success/failed）和验证数据计算 Runbook 有效性"""
    # 查询该 Runbook 的历史 execution 记录
    # 成功次数 / 总次数 = 有效率
```

---

## 四、文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `agent/agents/executor.py` | 命令执行器，白名单校验 + kubectl 执行 + 执行记录 |
| 新增 | `agent/agents/verify.py` | 恢复验证，轮询指标 + 超时升级 |
| 新增 | `agent/agents/report.py` | 报告生成，LLM 摘要 + Markdown 格式化 |
| 新增 | `agent/agents/audit.py` | 审计日志工具函数 |
| 新增 | `agent/middleware/__init__.py` | 中间件包 |
| 新增 | `agent/middleware/auth.py` | RBAC 中间件 |
| 新增 | `agent/api/v1/reports.py` | Report API |
| 新增 | `agent/api/v1/executions.py` | Execution 查询 API |
| 修改 | `agent/workflows/alert_workflow.py` | 新增 execute/verify/report 节点和路由 |
| 修改 | `agent/api/v1/approvals.py` | 审批通过后触发执行工作流 |
| 修改 | `agent/db/crud.py` | 新增 execution/report CRUD |
| 修改 | `agent/main.py` | 注册新路由和中间件 |

---

## 五、工作流状态流转

```
                    ┌──────────────────────────────────────┐
                    │          Phase 1 + Phase 2           │
                    │                                      │
  Alertmanager ──→ parse_alert ──→ collect_context        │
       Webhook         │                 │                  │
                       ↓                 ↓                  │
                   incident_id       context                │
                                           │                │
                                           ↓                │
                                      diagnose              │
                                           │                │
                                    ┌──────┴──────┐         │
                                    │  LLM诊断    │         │
                                    │  +Runbook   │         │
                                    │  +风险评估  │         │
                                    └──────┬──────┘         │
                                           │                │
                                    ~~~~~~~↓~~~~~~~~        │
                                    ╎ 飞书卡片审批 ╎        │
                                    ╎ 人工点击按钮 ╎        │
                                    ~~~~~~~┬~~~~~~~~        │
                                      ┌────┴────┐           │
                                      │ rejected│→ END      │
                                      │ escalated│→ END     │
                                      │ approved│           │
                                      └────┬────┘           │
                                           │                │
                    ┌──────────────────────┘                │
                    │         Phase 3 新增                  │
                    ↓                                       │
               execute ──────────────┐                     │
                    │                │ 执行失败              │
                    ↓                ↓                     │
               verify             escalate → END            │
                 │  │                                       │
            恢复 │  │ 超时                                   │
                 │  └──→ escalate → END                     │
                 ↓                                         │
              report → END                                 │
                                                           │
                    └──────────────────────────────────────┘
```

---

## 六、执行顺序

| 序号 | 模块 | 预估工时 | 依赖 |
|------|------|----------|------|
| 1 | Executor Agent + 白名单校验 | 2 天 | 无 |
| 2 | 审批回调触发执行工作流 | 1 天 | 1 |
| 3 | Verify Agent + 轮询验证 | 1.5 天 | 1 |
| 4 | 审计日志全链路埋点 | 0.5 天 | 1 |
| 5 | Report Agent + LLM 摘要 | 1 天 | 3 |
| 6 | Report API + Execution API | 0.5 天 | 5 |
| 7 | RBAC 中间件 | 0.5 天 | 无 |
| 8 | 历史故障库 + Runbook 评分 | 1 天 | 5 |
| 9 | Web Console 新增执行/报告页面 | 1 天 | 2, 6 |
| 10 | 端到端联调 | 1 天 | 全部 |
| **合计** | | **10 天** | |

---

## 七、关键技术决策

1. **工作流扩展而非重建**：在现有 `alert_workflow.py` 的 LangGraph 状态图上新增节点，审批通过后从 `diagnose` 之后继续流转，保持状态统一
2. **Executor 不做沙箱**：白名单 + 命令前缀匹配 已足够限制风险，不引入 Docker 沙箱增加复杂度
3. **Verify 轮询而非回调**：执行后轮询 Prometheus 指标验证恢复，简单可靠，不依赖 K8S Event Watch
4. **审计即埋点**：现有 `audit_logs` 表已定义，在各节点关键路径加一行 `write_audit()` 即可
5. **RBAC 最小化**：Header 传角色，不做完整认证，生产环境再接入 OAuth2
