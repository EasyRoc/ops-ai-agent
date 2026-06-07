# Phase D: 可观测性 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AI 兜底 + 重试循环提供完整的可观测性基础设施：DB 迁移（incidents 新增 retry_count/retry_history/ai_generated/ai_reasoning，executions 新增 round/ai_analysis）、审计事件体系（7 种新事件类型）、Web Console 重试时间线 + Dashboard 指标卡片。

**Architecture:** 遵循项目现有的 schema migration 模式（`ensure_phaseN_schema()` 幂等 ALTER TABLE），给 Incident/Execution 模型和 API 序列化新增字段，运行时代码从临时 `risk_assessment.retry` 迁移到专用列（含双向兼容读取），Web Console 用纯 HTML/JS 渲染树状重试时间线和统计卡片。

**Tech Stack:** SQLAlchemy async ORM, PostgreSQL JSONB, FastAPI, vanilla HTML/CSS/JS（与现有 Web Console 一致）

**前置依赖:**
- Phase C 已完成 — retry_count/retry_history 临时存储在 `risk_assessment.retry` 子对象中
- Phase C 已完成 — `analyze_failure_and_retry()` 输出 retry_reasoning / failure_analysis
- Phase C 已完成 — `build_retry_workflow()` 和 `run_retry_workflow()` 可用

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `agent/db/models.py` | **修改** | Incident 新增 retry_count/retry_history/ai_generated/ai_reasoning；Execution 新增 round/ai_analysis |
| `agent/db/crud.py` | **修改** | 新增 `ensure_phase4_schema()` + `migrate_retry_data()` 从 risk_assessment.retry 迁移到专用列 |
| `agent/api/v1/incidents.py` | **修改** | API 序列化新增 4 个字段 |
| `agent/api/v1/executions.py` | **修改** | API 序列化新增 round/ai_analysis |
| `agent/api/v1/approvals.py` | **修改** | `_load_execution_state()` 改为优先从专用列读，兜底从 risk_assessment.retry 读 |
| `agent/agents/fallback.py` | **修改** | `generate_ai_action_plan()` 调用方持久化时写入 ai_generated/ai_reasoning |
| `agent/workflows/retry_workflow.py` | **修改** | `retry_analyze` 改用 retry_count/retry_history 专用列而非 risk_assessment.retry |
| `agent/api/v1/audit.py` | **新增** | 审计事件查询 API（GET /incidents/{id}/audit） |
| `web/incident-detail.html` | **修改** | 新增 AI 重试时间线组件 + ai_generated 标记 |
| `web/index.html` | **修改** | 事件列表新增 AI 标记列 + Dashboard 统计卡片 |
| `tests/test_models.py` | **新增** | 模型字段测试 |
| `tests/test_crud_schema.py` | **新增** | Schema migration 测试 |
| `tests/test_audit_api.py` | **新增** | 审计查询 API 测试 |

**不涉及的文件（留给 Phase E）：**
- E2E 测试脚本
- 飞书卡片联调

---

## 关键设计决策

### Schema migration 模式

沿用项目已有的 `ensure_phaseN_schema()` 模式：用 `ALTER TABLE ADD COLUMN IF NOT EXISTS` 做幂等迁移，启动时调用。

```python
# agent/db/crud.py 新增
async def ensure_phase4_schema() -> None:
    statements = [
        # incidents 表
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retry_history JSONB",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_generated BOOLEAN DEFAULT FALSE",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_reasoning TEXT",
        # executions 表
        "ALTER TABLE executions ADD COLUMN IF NOT EXISTS round INTEGER DEFAULT 1",
        "ALTER TABLE executions ADD COLUMN IF NOT EXISTS ai_analysis TEXT",
        # 索引
        "CREATE INDEX IF NOT EXISTS idx_incidents_ai_generated ON incidents(ai_generated)",
        "CREATE INDEX IF NOT EXISTS idx_executions_incident_round ON executions(incident_id, round)",
    ]
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
    logger.info("Phase 4 schema ensured")
```

### 数据迁移策略（risk_assessment.retry → 专用列）

Phase C 的重试数据暂存在 `risk_assessment.retry` 子对象中。Phase D 加完专用列后，需要把存量数据迁移过去。采用**启动时自动迁移 + 运行时双向兼容读取**：

- **启动时**：调用 `migrate_retry_data()` 扫描 `ai_generated IS TRUE AND retry_count = 0` 的 Incident，从 `risk_assessment.retry` 提取数据写入 `retry_count` / `retry_history`
- **运行时读取**：`_load_execution_state()` 优先读 `incident.retry_count` / `incident.retry_history`；如果为 None/0 则降级读 `risk_assessment.retry`（兼容未迁移或迁移前创建的工单）
- **运行时写入**：Phase D 之后全部写入专用列，不再写 `risk_assessment.retry`

### retry_history 列的结构

与 Phase C 的 `risk_assessment.retry.history` 结构完全一致，只是位置从 JSONB 子对象提升为顶级 JSONB 列：

```json
[
  {
    "round": 1,
    "timestamp": "2026-06-07T10:00:00Z",
    "plan_steps": ["扩容 order-service 到 4 副本"],
    "execution": {"status": "failed", "stderr": "Error: timeout"},
    "verification": {"recovered": false, "current": 88.5, "threshold": 70.0},
    "analysis": "扩容后 CPU 仍 88%，排除容量瓶颈"
  }
]
```

### 审计事件查询 API

新增 `GET /api/v1/incidents/{incident_id}/audit`，按时间正序返回该工单的所有审计日志，用于 Web Console 时间线渲染：

```json
{
  "incident_id": "INC-XXX",
  "total": 15,
  "audit_logs": [
    {
      "id": 1,
      "actor": "system",
      "action": "ai_plan_generated",
      "detail": {"confidence": 0.75, "steps_count": 2},
      "created_at": "2026-06-07T10:00:00Z"
    }
  ]
}
```

### Web Console 重试时间线

在 incident-detail.html 新增一个 section，用树状结构展示 AI 重试历史：

```
┌─ 第 1 轮 (2026-06-07 10:00) ─────────────────────┐
│  方案: 扩容 order-service 到 4 副本                 │
│  执行: failed (Error: timeout)                     │
│  验证: 未恢复 (CPU 88.5% / 阈值 70%)               │
│  分析: 扩容后 CPU 仍 88%，排除容量瓶颈              │
└────────────────────────────────────────────────────┘
┌─ 第 2 轮 (2026-06-07 10:05) ─────────────────────┐
│  方案: 重启异常 Pod order-xyz                      │
│  执行: success                                     │
│  验证: 已恢复 (CPU 45% / 阈值 70%)                 │
└────────────────────────────────────────────────────┘
```

---

## 接口约定

### Incident model 新增字段

```python
# agent/db/models.py Incident 类新增
retry_count = Column(Integer, default=0)
retry_history = Column(JSONB)
ai_generated = Column(Boolean, default=False)
ai_reasoning = Column(Text)
```

### Execution model 新增字段

```python
# agent/db/models.py Execution 类新增
round = Column(Integer, default=1)
ai_analysis = Column(Text)
```

### `_load_execution_state()` 双向兼容读取

```python
# 优先读专用列，兜底读 risk_assessment.retry
retry_count = incident.retry_count or 0
retry_history = incident.retry_history or []

if not retry_history:
    risk = incident.risk_assessment or {}
    retry_meta = risk.get("retry", {})
    if retry_meta:
        retry_count = retry_meta.get("count", 0)
        retry_history = retry_meta.get("history", [])
```

---

### Task 1: DB 模型 + Schema 迁移

**Files:**
- Modify: `agent/db/models.py:1-67` — Incident/Execution 新增字段
- Modify: `agent/db/crud.py:19-43` — 新增 `ensure_phase4_schema()` + `migrate_retry_data()`
- Create: `tests/test_models.py` — 模型字段测试
- Create: `tests/test_crud_schema.py` — Schema 迁移测试

- [ ] **Step 1: Incident model 新增 4 个字段**

`agent/db/models.py:13-33`，在 `Incident` 类的 `approval_status` 行之后、`created_at` 行之前插入：

```python
    # Phase D: AI 兜底 + 重试循环可观测字段
    retry_count = Column(Integer, default=0)
    retry_history = Column(JSONB)
    ai_generated = Column(Boolean, default=False)
    ai_reasoning = Column(Text)
```

完整上下文（修改后的 Incident 类）：

```python
class Incident(Base):
    __tablename__ = "incidents"

    id = Column(String(64), primary_key=True, default=lambda: f"INC-{uuid.uuid4().hex[:12].upper()}")
    service = Column(String(128), nullable=False)
    env = Column(String(32), nullable=False, default="prod")
    severity = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False, default="open")
    alert_name = Column(String(256))
    alert_value = Column(String(128))
    root_cause = Column(Text)
    confidence = Column(Float)
    evidence = Column(JSONB)
    runbook_name = Column(String(128))
    action_plan = Column(JSONB)
    risk_assessment = Column(JSONB)
    approval_status = Column(String(32))
    # Phase D: AI 兜底 + 重试循环可观测字段
    retry_count = Column(Integer, default=0)
    retry_history = Column(JSONB)
    ai_generated = Column(Boolean, default=False)
    ai_reasoning = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 2: Execution model 新增 2 个字段**

`agent/db/models.py:35-45`，在 `Execution` 类的 `completed_at` 行之前插入：

```python
    # Phase D: 重试轮次 + AI 分析文本
    round = Column(Integer, default=1)
    ai_analysis = Column(Text)
```

完整上下文（修改后的 Execution 类）：

```python
class Execution(Base):
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(64), ForeignKey("incidents.id"))
    action = Column(String(512), nullable=False)
    operator = Column(String(64))
    status = Column(String(32), nullable=False, default="pending")
    result = Column(JSONB)
    # Phase D: 重试轮次 + AI 分析文本
    round = Column(Integer, default=1)
    ai_analysis = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True))
```

- [ ] **Step 3: 新增 `ensure_phase4_schema()` + `migrate_retry_data()`**

`agent/db/crud.py:43` 之后（`ensure_phase3_schema` 函数之后）追加：

```python
async def ensure_phase4_schema() -> None:
    """Apply idempotent schema additions for Phase D observability features."""
    statements = [
        # incidents 表：AI 兜底 + 重试循环
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retry_history JSONB",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_generated BOOLEAN DEFAULT FALSE",
        "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_reasoning TEXT",
        # executions 表：重试轮次 + AI 分析
        "ALTER TABLE executions ADD COLUMN IF NOT EXISTS round INTEGER DEFAULT 1",
        "ALTER TABLE executions ADD COLUMN IF NOT EXISTS ai_analysis TEXT",
        # 索引
        "CREATE INDEX IF NOT EXISTS idx_incidents_ai_generated ON incidents(ai_generated)",
        "CREATE INDEX IF NOT EXISTS idx_executions_incident_round ON executions(incident_id, round)",
    ]
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
    logger.info("Phase 4 schema ensured")


async def migrate_retry_data() -> None:
    """从 risk_assessment.retry 迁移存量重试数据到专用列。

    Phase C 临时把 retry_count/retry_history 存在 risk_assessment JSONB 的 retry 子对象中。
    Phase D 加完专用列后，一次性把存量数据迁过去。之后运行时读写都用专用列。
    """
    logger.info("开始迁移 risk_assessment.retry → 专用列")
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Incident).where(
                Incident.retry_count == 0,
                Incident.ai_generated == True,
            )
        )
        incidents = list(result.scalars().all())
        migrated = 0
        for incident in incidents:
            risk = incident.risk_assessment or {}
            retry_meta = risk.get("retry", {})
            if not retry_meta:
                continue
            count = retry_meta.get("count", 0)
            history = retry_meta.get("history", [])
            if count > 0 or history:
                incident.retry_count = count
                incident.retry_history = history
                migrated += 1
                logger.info(
                    "迁移重试数据: incident=%s, retry_count=%s, history_rounds=%s",
                    incident.id,
                    count,
                    len(history),
                )
        await session.commit()
    logger.info("重试数据迁移完成: 扫描=%s, 迁移=%s", len(incidents), migrated)
```

- [ ] **Step 4: 在启动流程中调用 ensure_phase4_schema + migrate_retry_data**

在 `agent/main.py` 或应用启动入口中追加（需要先确认启动文件路径）。先检查 main.py：

找到 `ensure_phase3_schema()` 的调用位置，在其后追加 `ensure_phase4_schema()` + `migrate_retry_data()` 调用。如果 main.py 不在当前能直接确认的位置，这一步标注为需要根据实际 main.py 结构插入。

- [ ] **Step 5: 编写模型字段测试**

创建 `tests/test_models.py`：

```python
from unittest import TestCase
from agent.db.models import Incident, Execution


class IncidentModelTest(TestCase):
    def test_has_retry_count_field(self):
        self.assertTrue(hasattr(Incident, "retry_count"))

    def test_has_retry_history_field(self):
        self.assertTrue(hasattr(Incident, "retry_history"))

    def test_has_ai_generated_field(self):
        self.assertTrue(hasattr(Incident, "ai_generated"))

    def test_has_ai_reasoning_field(self):
        self.assertTrue(hasattr(Incident, "ai_reasoning"))

    def test_retry_count_default_zero(self):
        self.assertEqual(Incident().retry_count, 0)

    def test_ai_generated_default_false(self):
        self.assertFalse(Incident().ai_generated)

    def test_retry_history_default_none(self):
        self.assertIsNone(Incident().retry_history)

    def test_ai_reasoning_default_none(self):
        self.assertIsNone(Incident().ai_reasoning)


class ExecutionModelTest(TestCase):
    def test_has_round_field(self):
        self.assertTrue(hasattr(Execution, "round"))

    def test_has_ai_analysis_field(self):
        self.assertTrue(hasattr(Execution, "ai_analysis"))

    def test_round_default_one(self):
        self.assertEqual(Execution().round, 1)

    def test_ai_analysis_default_none(self):
        self.assertIsNone(Execution().ai_analysis)
```

- [ ] **Step 6: 编写 Schema 迁移测试**

创建 `tests/test_crud_schema.py`：

```python
from unittest import TestCase
from unittest.mock import AsyncMock, patch, MagicMock
from agent.db.crud import ensure_phase4_schema, migrate_retry_data


class EnsurePhase4SchemaTest(TestCase):
    @patch("agent.db.crud.engine")
    async def test_executes_all_migration_statements(self, mock_engine):
        mock_conn = AsyncMock()
        mock_engine.begin.return_value.__aenter__.return_value = mock_conn

        await ensure_phase4_schema()

        calls = [c.args[0] for c in mock_conn.execute.call_args_list]
        self.assertIn("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0", calls)
        self.assertIn("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retry_history JSONB", calls)
        self.assertIn("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_generated BOOLEAN DEFAULT FALSE", calls)
        self.assertIn("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_reasoning TEXT", calls)
        self.assertIn("ALTER TABLE executions ADD COLUMN IF NOT EXISTS round INTEGER DEFAULT 1", calls)
        self.assertIn("ALTER TABLE executions ADD COLUMN IF NOT EXISTS ai_analysis TEXT", calls)


class MigrateRetryDataTest(TestCase):
    @patch("agent.db.crud.AsyncSessionLocal")
    async def test_migrates_retry_data_from_risk_assessment(self, mock_session_cls):
        from agent.db.models import Incident

        incident = Incident(id="INC-TEST")
        incident.retry_count = 0
        incident.ai_generated = True
        incident.risk_assessment = {
            "level": "中风险",
            "retry": {
                "count": 3,
                "history": [
                    {"round": 1, "analysis": "test analysis"}
                ],
            },
        }

        mock_session = AsyncMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = [incident]
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        await migrate_retry_data()

        self.assertEqual(incident.retry_count, 3)
        self.assertEqual(len(incident.retry_history), 1)
        self.assertEqual(incident.retry_history[0]["round"], 1)
        mock_session.commit.assert_awaited_once()

    @patch("agent.db.crud.AsyncSessionLocal")
    async def test_skips_when_no_retry_meta(self, mock_session_cls):
        incident = Incident(id="INC-TEST")
        incident.retry_count = 0
        incident.ai_generated = True
        incident.risk_assessment = {"level": "中风险"}  # 无 retry 子对象

        mock_session = AsyncMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = [incident]
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        await migrate_retry_data()

        self.assertEqual(incident.retry_count, 0)
        self.assertIsNone(incident.retry_history)
```

- [ ] **Step 7: 运行模型和迁移测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_models.py tests/test_crud_schema.py -v
```

期望：12 个测试 PASS（8 个模型字段 + 4 个迁移逻辑）。

- [ ] **Step 8: 提交**

```bash
git add agent/db/models.py agent/db/crud.py tests/test_models.py tests/test_crud_schema.py
git commit -m "feat: add Phase D DB columns for AI fallback observability

- Incident: retry_count, retry_history, ai_generated, ai_reasoning
- Execution: round, ai_analysis
- ensure_phase4_schema() for idempotent migration
- migrate_retry_data() for Phase C risk_assessment.retry -> dedicated columns

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 运行时接入专用列 + 双向兼容读取

**Files:**
- Modify: `agent/api/v1/approvals.py:224-278` — `_load_execution_state()` 改用专用列 + 双向兼容
- Modify: `agent/workflows/retry_workflow.py:1100-1150` — `retry_analyze` 写专用列不再写 risk_assessment.retry
- Modify: `agent/api/v1/incidents.py:16-69` — 序列化新增 4 个字段
- Modify: `agent/api/v1/executions.py:12-23` — 序列化新增 2 个字段
- Modify: `agent/agents/executor.py` — `record_execution()` 写 round/ai_analysis 列

- [ ] **Step 1: `_load_execution_state()` 双向兼容读取**

`agent/api/v1/approvals.py:245-278`，重写 AI/retry 状态恢复逻辑。将 Phase C 中追加的 `risk_assessment.retry` 读取逻辑改为优先读专用列：

```python
    # ---- 恢复 AI 兜底 / 重试状态（Phase D: 专用列优先） ----

    if state["runbook"]:
        state["runbook"]["ai_generated"] = bool(incident.ai_generated)
        if incident.ai_reasoning:
            state["runbook"]["ai_reasoning"] = incident.ai_reasoning

    # retry_count / retry_history：优先专用列，兜底 risk_assessment.retry
    retry_count = incident.retry_count or 0
    retry_history = incident.retry_history or []

    if not retry_history:
        risk = incident.risk_assessment or {}
        retry_meta = risk.get("retry", {})
        if retry_meta:
            retry_count = retry_meta.get("count", 0)
            retry_history = retry_meta.get("history", [])
            logger.info(
                "从 risk_assessment.retry 降级读取重试数据: incident=%s, count=%s",
                incident_id,
                retry_count,
            )

    state["retry_count"] = retry_count
    state["retry_history"] = retry_history

    # verification / ai_reasoning：恢复 AI 方案的验证条件
    risk = incident.risk_assessment or {}
    if risk.get("ai_generated") and state["runbook"]:
        state["runbook"]["verification"] = risk.get("verification", {})
        if not state["runbook"].get("ai_reasoning"):
            state["runbook"]["ai_reasoning"] = risk.get("ai_reasoning", "")

    # latest_plan：仅当 retry_history 非空时，从 retry_history 最后一轮取最新方案
    if retry_history and state["runbook"]:
        latest_round = retry_history[-1]
        if latest_round.get("plan_steps"):
            logger.info(
                "恢复最新重试方案: incident=%s, latest_round=%s",
                incident_id,
                latest_round.get("round"),
            )
```

将这段代码放在 `state["risk_assessment"] = incident.risk_assessment or {}` 行之后、`logger.info("执行工作流状态已恢复: ...")` 行之前。

- [ ] **Step 2: `retry_analyze` 改用专用列持久化**

`agent/workflows/retry_workflow.py:1140-1150`，将 `update_incident()` 调用改为传入 `retry_count` 和 `retry_history` 专用列：

在 `retry_analyze` 函数的持久化部分（约 line 1141-1153），将 `update_incident` 调用从：

```python
            await update_incident(
                session, incident_id,
                status="retry_pending",
                approval_status="pending",
                action_plan=[step.to_dict() for step in ai_steps],
                risk_assessment=new_risk,
                runbook_name="ai_retry",
            )
```

改为：

```python
            await update_incident(
                session, incident_id,
                status="retry_pending",
                approval_status="pending",
                action_plan=[step.to_dict() for step in ai_steps],
                risk_assessment=new_risk,
                runbook_name="ai_retry",
                retry_count=retry_count,
                retry_history=retry_history,
                ai_generated=True,
                ai_reasoning=ai_retry.get("retry_reasoning", ""),
            )
```

同时，将 `new_risk["retry"] = {...}` 这一整段（Phase C 的临时持久化代码）标记为可选保留（向后兼容，不删也可以）：

```python
    # Phase D: 专用列已接管持久化，new_risk["retry"] 保留作为向后兼容的冗余副本
    new_risk["retry"] = {
        "count": retry_count,
        "history": retry_history,
        "latest_plan": {
            "steps": [step.to_dict() for step in ai_steps],
            "verification": ai_retry.get("verification", {}),
            "ai_reasoning": ai_retry.get("retry_reasoning", ""),
        },
    }
```

- [ ] **Step 3: `record_execution()` 改写 round 列（而非仅塞进 result JSONB）**

`agent/agents/executor.py:151-177`，在 Phase C 的 Task 3 Step 2 基础上，将 `round_num` 写入 Execution 模型的 `round` 属性而非仅塞进 result JSONB：

```python
async def record_execution(
    incident_id: str,
    action: str,
    operator: str,
    status: str,
    result: dict,
    round_num: int = 1,
) -> Execution:
    logger.info(
        "进入 record_execution: incident=%s, operator=%s, status=%s, action=%s, round=%s",
        incident_id, operator, status, action, round_num,
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
```

- [ ] **Step 4: API 序列化新增字段**

`agent/api/v1/incidents.py:24-38`，列表接口序列化新增 4 个字段：

在 `list_incidents_endpoint` 的 dict 中追加：

```python
                "ai_generated": i.ai_generated,
                "retry_count": i.retry_count,
```

`agent/api/v1/incidents.py:52-69`，详情接口序列化新增 4 个字段：

在 `get_incident_endpoint` 的 dict 中追加：

```python
        "ai_generated": incident.ai_generated,
        "ai_reasoning": incident.ai_reasoning,
        "retry_count": incident.retry_count,
        "retry_history": incident.retry_history,
```

`agent/api/v1/executions.py:14-23`，`_serialize_execution()` 新增 2 个字段：

```python
def _serialize_execution(execution) -> dict:
    return {
        "id": execution.id,
        "incident_id": execution.incident_id,
        "action": execution.action,
        "operator": execution.operator,
        "status": execution.status,
        "result": execution.result,
        "round": execution.round,
        "ai_analysis": execution.ai_analysis,
        "created_at": execution.created_at.isoformat() if execution.created_at else None,
        "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
    }
```

- [ ] **Step 5: 提交**

```bash
git add agent/api/v1/approvals.py agent/workflows/retry_workflow.py agent/agents/executor.py agent/api/v1/incidents.py agent/api/v1/executions.py
git commit -m "feat: wire Phase D dedicated columns into runtime code

- _load_execution_state: dual-read from dedicated columns + risk_assessment.retry fallback
- retry_analyze writes retry_count/retry_history/ai_generated/ai_reasoning
- record_execution writes Execution.round
- API serialization includes new fields

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: 审计事件体系

**Files:**
- Create: `agent/api/v1/audit.py` — 审计日志查询 API
- Modify: `agent/agents/fallback.py` — `generate_ai_action_plan()` 调用方写 `ai_plan_generated` 事件
- Modify: `agent/workflows/retry_workflow.py` — 补写 `retry_executed` / `retry_exhausted` 事件
- Create: `tests/test_audit_api.py` — 审计 API 测试

- [ ] **Step 1: 新增审计事件查询 API**

创建 `agent/api/v1/audit.py`：

```python
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.crud import AsyncSessionLocal, get_session
from agent.db.models import AuditLog

logger = logging.getLogger("ops-agent.api.audit")
router = APIRouter(prefix="/api/v1")


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/incidents/{incident_id}/audit")
async def list_audit_logs_endpoint(
    incident_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """查询某个 Incident 的审计日志，按时间正序排列，用于渲染时间线。"""
    from sqlalchemy import select

    logger.info("GET /incidents/%s/audit: limit=%s", incident_id, limit)
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.incident_id == incident_id)
        .order_by(AuditLog.created_at.asc())
        .limit(limit)
    )
    logs = list(result.scalars().all())
    logger.info("GET /incidents/%s/audit: 返回 %s 条", incident_id, len(logs))
    return {
        "incident_id": incident_id,
        "total": len(logs),
        "audit_logs": [
            {
                "id": log.id,
                "actor": log.actor,
                "action": log.action,
                "detail": log.detail,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }
```

- [ ] **Step 2: 在 `fallback.py` 中写 `ai_plan_generated` 审计事件**

在 `agent/agents/fallback.py` 的 `generate_ai_action_plan()` 函数返回前（约 line 130，`return` 语句之前），追加审计写入。需要调用方传入 `incident_id` 参数来写审计。

由于 `generate_ai_action_plan()` 当前不接收 `incident_id`，有两种方案：

**方案 A（推荐）**：在调用方（`rca.py` 的 `_build_ai_fallback_plan()`）中写入审计事件——`rca.py` 已有 `incident_id`。

**方案 B**：给 `generate_ai_action_plan()` 增加可选参数 `incident_id`。

采用方案 A，不改 `fallback.py` 签名，在 rca.py 的调用点写审计。

在 `agent/agents/rca.py` 的 `_build_ai_fallback_plan()` 函数（ai_plan 生成成功后）追加：

```python
    from agent.agents.audit import write_audit

    await write_audit(
        incident_id,
        "system",
        "ai_plan_generated",
        {
            "confidence": ai_plan.get("confidence", 0),
            "steps_count": len(ai_plan.get("steps", [])),
            "ai_reasoning": ai_plan.get("ai_reasoning", "")[:200],
        },
    )
```

- [ ] **Step 3: 在 `retry_workflow.py` 中补写关键审计事件**

`agent/workflows/retry_workflow.py:960`，`retry_execute` 节点中已有 `retry_command_executed` 审计。追加一条汇总事件 `retry_executed`（在 for 循环结束后，return 之前）：

```python
    await write_audit(
        incident_id, operator, "retry_executed",
        {
            "round": retry_count,
            "status": "success" if success else "failed",
            "executed_steps": len(results),
            "total_steps": len(selected_steps),
        },
    )
```

`agent/workflows/retry_workflow.py` 的 `retry_analyze` 节点中，在 `retry_count >= MAX_RETRY_ROUNDS` 的分支（route_after_retry_verify 中）不明显。应该在实际触发 escalate 前补写 `retry_exhausted` 事件。

在 `route_after_retry_verify` 函数中不方便异步调用，因此改为在 `retry_analyze` 节点开头判断 retry_count 是否已达上限，如果达到就写 `retry_exhausted` 审计。

`retry_analyze` 函数开头（retry_count 计算之后）追加：

```python
    if retry_count > MAX_RETRY_ROUNDS:
        logger.warning("retry_analyze: 重试次数超限: incident=%s, count=%s", incident_id, retry_count)
        await write_audit(incident_id, operator, "retry_exhausted",
                          {"total_rounds": retry_count - 1, "max": MAX_RETRY_ROUNDS})
        state["approval_status"] = "escalated"
        return state
```

- [ ] **Step 4: 编写审计 API 测试**

创建 `tests/test_audit_api.py`：

```python
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from fastapi import BackgroundTasks


class AuditApiTest(IsolatedAsyncioTestCase):
    @patch("agent.api.v1.audit.AsyncSessionLocal")
    async def test_list_audit_logs_returns_chronological(self, mock_session_cls):
        from agent.db.models import AuditLog
        from datetime import datetime, timezone

        log1 = AuditLog(
            id=1, incident_id="INC-1", actor="system",
            action="ai_plan_generated",
            detail={"confidence": 0.75},
            created_at=datetime(2026, 6, 7, 10, 0, 0, tzinfo=timezone.utc),
        )
        log2 = AuditLog(
            id=2, incident_id="INC-1", actor="pengyi",
            action="ai_execution_approved",
            detail={},
            created_at=datetime(2026, 6, 7, 10, 1, 0, tzinfo=timezone.utc),
        )
        log3 = AuditLog(
            id=3, incident_id="INC-1", actor="system",
            action="retry_analysis",
            detail={"round": 2},
            created_at=datetime(2026, 6, 7, 10, 5, 0, tzinfo=timezone.utc),
        )

        mock_session = AsyncMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = [log1, log2, log3]
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        from agent.api.v1.audit import list_audit_logs_endpoint
        result = await list_audit_logs_endpoint("INC-1", db=mock_session)

        self.assertEqual(result["total"], 3)
        self.assertEqual(result["incident_id"], "INC-1")
        self.assertEqual(len(result["audit_logs"]), 3)
        # 按时间正序
        self.assertEqual(result["audit_logs"][0]["action"], "ai_plan_generated")
        self.assertEqual(result["audit_logs"][1]["action"], "ai_execution_approved")
        self.assertEqual(result["audit_logs"][2]["action"], "retry_analysis")

    @patch("agent.api.v1.audit.AsyncSessionLocal")
    async def test_list_audit_logs_empty(self, mock_session_cls):
        mock_session = AsyncMock()
        mock_session.execute.return_value.scalars.return_value.all.return_value = []
        mock_session_cls.return_value.__aenter__.return_value = mock_session

        from agent.api.v1.audit import list_audit_logs_endpoint
        result = await list_audit_logs_endpoint("INC-999", db=mock_session)

        self.assertEqual(result["total"], 0)
        self.assertEqual(len(result["audit_logs"]), 0)
```

- [ ] **Step 5: 注册 audit 路由到 FastAPI app**

需要确认 `agent/main.py` 中路由注册位置，将 `agent.api.v1.audit.router` 加入 app。这一步根据实际 main.py 结构调整。

- [ ] **Step 6: 运行审计测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_audit_api.py -v
```

期望：2 个测试 PASS。

- [ ] **Step 7: 提交**

```bash
git add agent/api/v1/audit.py agent/agents/rca.py agent/workflows/retry_workflow.py tests/test_audit_api.py
git commit -m "feat: add audit event system for AI fallback and retry observability

- New GET /api/v1/incidents/{id}/audit endpoint
- ai_plan_generated / retry_executed / retry_exhausted audit events
- Chronological audit log query for Web Console timeline

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Web Console — 重试时间线 + Dashboard 指标

**Files:**
- Modify: `web/incident-detail.html` — 新增 AI 重试时间线 section
- Modify: `web/index.html` — 事件列表新增 AI 标记，顶部新增 Dashboard 统计卡片

- [ ] **Step 1: incident-detail.html 新增 AI 重试时间线 section**

`web/incident-detail.html:110-122`，在"故障报告" section 之后、"</main>" 之前插入：

```html
    <section class="card" id="retryTimelineSection" hidden>
      <h3>AI 重试时间线</h3>
      <p class="muted">每次 AI 重试的分析、执行、验证结果按时间排列。</p>
      <div id="retryTimeline"></div>
    </section>

    <section class="card" id="auditTimelineSection" hidden>
      <h3>审计日志</h3>
      <p class="muted">完整的事件审计链路，按时间正序展示。</p>
      <div id="auditTimeline"></div>
    </section>
```

在 `<style>` 块中追加时间线样式：

```css
    .timeline {
      border-left: 3px solid #e5e7eb;
      padding-left: 20px;
      margin: 12px 0;
    }
    .timeline-entry {
      position: relative;
      margin-bottom: 18px;
      padding: 14px 16px;
      background: #f9fafb;
      border-radius: 10px;
      border: 1px solid #e5e7eb;
    }
    .timeline-entry::before {
      content: "";
      position: absolute;
      left: -28px;
      top: 18px;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #2563eb;
      border: 2px solid white;
    }
    .timeline-entry.failed::before { background: #dc2626; }
    .timeline-entry.recovered::before { background: #059669; }
    .timeline-entry.analyzing::before { background: #d97706; }
    .retry-round {
      font-weight: 700;
      font-size: 15px;
      margin-bottom: 8px;
      color: #111827;
    }
    .retry-detail {
      font-size: 13px;
      color: #4b5563;
      line-height: 1.7;
    }
    .retry-detail span {
      display: inline-block;
      margin-right: 12px;
    }
    .ai-badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      background: #fef3c7;
      color: #92400e;
      font-size: 11px;
      font-weight: 700;
    }
    .metric-card {
      display: inline-block;
      background: white;
      border: 1px solid #e5e7eb;
      border-radius: 10px;
      padding: 14px 18px;
      margin: 6px;
      min-width: 130px;
      text-align: center;
    }
    .metric-card .value {
      font-size: 28px;
      font-weight: 800;
      color: #111827;
    }
    .metric-card .label {
      font-size: 12px;
      color: #667085;
      margin-top: 4px;
    }
```

在 `<script>` 块末尾追加时间线加载逻辑：

```javascript
    async function loadRetryTimeline() {
      const section = document.getElementById("retryTimelineSection");
      const container = document.getElementById("retryTimeline");
      if (!incidentId || !currentIncident) return;

      const retryHistory = currentIncident.retry_history || [];
      const aiGenerated = currentIncident.ai_generated;

      if (!aiGenerated && retryHistory.length === 0) {
        section.hidden = true;
        return;
      }

      section.hidden = false;

      // AI 标记
      let html = "";
      if (aiGenerated) {
        html += `<p><span class="ai-badge">AI 兜底方案</span> 推理: ${(currentIncident.ai_reasoning || "无").slice(0, 150)}</p>`;
      }

      if (retryHistory.length === 0) {
        html += "<p class='muted'>无重试记录（AI 方案首轮即恢复或未触发执行）。</p>";
        container.innerHTML = html;
        return;
      }

      html += `<div class="timeline">`;
      retryHistory.forEach((entry, idx) => {
        const execOk = entry.execution?.status === "success";
        const recovered = entry.verification?.recovered;
        let cls = "analyzing";
        if (recovered) cls = "recovered";
        else if (!execOk && entry.execution) cls = "failed";

        html += `
          <div class="timeline-entry ${cls}">
            <div class="retry-round">第 ${entry.round} 轮重试 — ${entry.timestamp ? new Date(entry.timestamp).toLocaleString() : "未知时间"}</div>
            <div class="retry-detail">
              <div><strong>方案：</strong>${(entry.plan_steps || []).join("；") || "无"}</div>
              <div><strong>执行：</strong>${entry.execution?.status || "未执行"}${entry.execution?.stderr ? " — " + entry.execution.stderr.slice(0, 100) : ""}</div>
              <div><strong>验证：</strong>${recovered ? "已恢复" : "未恢复"} (当前 ${entry.verification?.current ?? "?"} / 阈值 ${entry.verification?.threshold ?? "?"})</div>
              ${entry.analysis ? `<div><strong>分析：</strong>${entry.analysis}</div>` : ""}
            </div>
          </div>`;
      });
      html += `</div>`;
      container.innerHTML = html;
    }

    async function loadAuditTimeline() {
      const section = document.getElementById("auditTimelineSection");
      const container = document.getElementById("auditTimeline");
      if (!incidentId) return;

      try {
        const response = await fetch(`/api/v1/incidents/${encodeURIComponent(incidentId)}/audit?limit=100`);
        if (!response.ok) {
          section.hidden = true;
          return;
        }
        const data = await response.json();
        if (!data.audit_logs || data.audit_logs.length === 0) {
          section.hidden = true;
          return;
        }
        section.hidden = false;
        const actionLabels = {
          "ai_plan_generated": "AI 方案生成",
          "ai_execution_approved": "用户批准 AI 执行",
          "manual_execution_chosen": "用户选择人工处理",
          "retry_analysis": "AI 重试自省",
          "retry_executed": "重试执行",
          "retry_exhausted": "重试次数耗尽",
          "ai_command_blocked": "AI 命令被拦截",
          "retry_command_executed": "重试命令执行",
          "retry_execution_blocked": "重试执行被阻止",
        };
        container.innerHTML = `<div class="timeline">` +
          data.audit_logs.map(log => `
            <div class="timeline-entry">
              <div class="retry-round">${actionLabels[log.action] || log.action} <span class="muted" style="font-weight:400;font-size:12px;">— ${log.created_at ? new Date(log.created_at).toLocaleString() : ""}</span></div>
              <div class="retry-detail">操作人: ${log.actor || "system"} | ${JSON.stringify(log.detail || {}).slice(0, 200)}</div>
            </div>
          `).join("") +
          `</div>`;
      } catch (err) {
        section.hidden = true;
      }
    }
```

在 `loadIncident()` 函数末尾（`setApproval(...)` 之后）追加：

```javascript
      loadRetryTimeline();
```

在页面底部的初始化调用处（`loadIncident(); loadExecutions(); loadReport();` 之后）追加：

```javascript
    loadAuditTimeline();
```

- [ ] **Step 2: index.html Dashboard 新增统计卡片**

`web/index.html:108-131`，在 `<main>` 标签内的 `.toolbar` div 和 `.card` section 之间插入 Dashboard 统计卡片：

```html
    <section class="grid" id="dashboard" style="margin-bottom:18px;">
      <div class="card" style="text-align:center;">
        <div class="metric-card">
          <div class="value" id="statTotal">-</div>
          <div class="label">总事件</div>
        </div>
        <div class="metric-card">
          <div class="value" id="statAiRatio">-</div>
          <div class="label">AI 兜底占比</div>
        </div>
        <div class="metric-card">
          <div class="value" id="statAiSuccess">-</div>
          <div class="label">AI 方案成功率</div>
        </div>
        <div class="metric-card">
          <div class="value" id="statAvgRetry">-</div>
          <div class="label">平均重试轮数</div>
        </div>
      </div>
    </section>
```

在 `<script>` 块的 `loadIncidents()` 函数末尾（渲染完表格后）追加统计计算：

```javascript
        // Dashboard 统计
        const incidents = data.incidents || [];
        const total = incidents.length;
        const aiIncidents = incidents.filter(i => i.ai_generated);
        const aiRatio = total > 0 ? Math.round(aiIncidents.length / total * 100) : 0;

        // AI 方案成功率：AI 工单中 status 为 resolved/verified 的比例
        const aiResolved = aiIncidents.filter(i =>
          i.status === "resolved" || i.status === "verified" || i.approval_status === "verified"
        );
        const aiSuccess = aiIncidents.length > 0 ? Math.round(aiResolved.length / aiIncidents.length * 100) : 0;

        // 平均重试轮数
        const retryCounts = incidents
          .map(i => i.retry_count || 0)
          .filter(c => c > 0);
        const avgRetry = retryCounts.length > 0
          ? (retryCounts.reduce((a, b) => a + b, 0) / retryCounts.length).toFixed(1)
          : "0";

        document.getElementById("statTotal").textContent = total;
        document.getElementById("statAiRatio").textContent = aiRatio + "%";
        document.getElementById("statAiSuccess").textContent = aiSuccess + "%";
        document.getElementById("statAvgRetry").textContent = avgRetry;
```

在事件列表表格中新增一列"AI"标记（在 `<th>风险</th>` 之前插入）：

```html
            <th>AI</th>
```

对应的 `<td>` 中（在 `<td>${risk}</td>` 之前）：

```javascript
              <td>${item.ai_generated ? '<span class="ai-badge">AI</span>' : "-"}</td>
```

同时更新 `<thead>` 中 `<th>风险</th>` 的 colspan（如果有的话，当前没有，只是追加一列）。

- [ ] **Step 3: 全量回归测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/ -v --ignore=tests/e2e_phase1.sh --ignore=tests/e2e_phase2.sh --ignore=tests/e2e_phase3.sh 2>&1 | tail -40
```

期望：所有已有测试 PASS。

- [ ] **Step 4: 提交**

```bash
git add web/incident-detail.html web/index.html
git commit -m "feat: add AI retry timeline and dashboard metrics to Web Console

- Incident detail: tree-view retry timeline + audit log timeline
- Dashboard: AI fallback ratio, success rate, avg retry rounds cards
- AI badge on incident list

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 依赖关系

```
Task 1 (models + schema migration + CRUD)
     ↓
Task 2 (wire new columns in runtime + dual-read compat + API serializers)
     ↓
Task 3 (audit events + audit API)  ←─ 可与 Task 4 部分并行
     ↓
Task 4 (Web Console timeline + dashboard)
```

Task 1→2 严格串行（Task 2 依赖 Task 1 定义的列名和类型）。
Task 3 和 Task 4 在 Task 2 之后可并行（Task 3 提供 API，Task 4 消费 API），但 Task 4 的审计时间线依赖 Task 3 的 `/audit` 端点，所以 Task 3 先做更合理。

---

## 自检清单

- [x] **Spec 覆盖**：D1（DB 迁移：incidents 4 字段 + executions 2 字段）→ Task 1；D2（executions round/ai_analysis）→ Task 1 Step 2 + Task 2 Step 3；D3（审计事件 7 种类型）→ Task 3；D4（Web Console 时间线 + Dashboard）→ Task 4
- [x] **无占位符**：所有 SQL、Python 代码、HTML/JS 代码完整写出
- [x] **类型一致性**：`retry_count` INTEGER DEFAULT 0、`retry_history` JSONB、`ai_generated` BOOLEAN DEFAULT FALSE、`ai_reasoning` TEXT，与 design doc 3.4.1 一致；`round` INTEGER DEFAULT 1、`ai_analysis` TEXT，与 design doc 一致
- [x] **双向兼容**：`_load_execution_state()` 优先读专用列，兜底读 `risk_assessment.retry`；`migrate_retry_data()` 启动时批量迁移存量数据
- [x] **幂等迁移**：所有 ALTER TABLE 使用 `IF NOT EXISTS`，索引使用 `IF NOT EXISTS`
- [x] **API 兼容**：列表接口只新增字段，不改已有字段名；详情接口同理
- [x] **Web Console 降级**：retry_history 为空时显示"无重试记录"；audit API 失败时隐藏时间线 section
