# AI 兜底策略 + 自主重试优化方案

> 版本：v0.1 | 日期：2026-06-06 | 状态：草案

## 一、问题分析

### 1.1 当前瓶颈

Phase 1-3 实现了完整的 告警→诊断→Runbook→审批→执行→验证→报告 闭环，但存在一个关键缺口：

```
告警进入 → load_runbook() 关键词匹配 → 命中 → 正常流程
                                      → 未命中 → 返回 None → 飞书卡片显示"未匹配到 Runbook"
                                               → 无处置方案、无审批按钮 → 工单悬空
```

**现状数据**：`runbooks/` 目录仅有 4 个 Runbook（cpu_high、error_rate、latency_high、oom），`ALERT_TO_RUNBOOK` 仅 12 个关键词映射。任何超出这 4 类的告警（磁盘满、网络分区、连接池耗尽、线程池满、GC 频繁、中间件异常……）都无法自动处置。

### 1.2 核心诉求

| 诉求 | 说明 |
|------|------|
| **AI 兜底** | 告警未命中 Runbook 时，Agent 调用 LLM 自主分析上下文，生成处置方案 |
| **人工决策** | AI 生成的方案推送到飞书，用户可选择"让 AI 自动执行"或"我自己来" |
| **失败自愈** | AI 执行失败后，自省失败原因，修正方案重试，最多 5 轮 |
| **全程可观测** | 每轮分析、执行、失败原因、重试计数均可追溯 |

---

## 二、整体方案

### 2.1 改造后的完整流程

```
告警 → 采集上下文 → LLM 根因分析
                          ↓
              ┌─ Runbook 命中？ ─────────────────────┐
              ↓ 是                                   ↓ 否（AI 兜底）
        匹配 Runbook                           Fallback Agent
        风险评估                               LLM 自主生成方案
              ↓                                      ↓
        飞书诊断卡片                          飞书 AI 兜底卡片
        [批准执行][拒绝][转人工]               [AI自动执行][我自己来][拒绝]
              ↓                                      ↓
        自动执行 kubectl                       ├─ "我自己来" → 发 kubectl 命令文本 → 人工执行 → 验证
              ↓                                └─ "AI自动执行" → 执行 kubectl
        恢复验证                                    ↓
              ↓                               成功？→ 恢复验证 → 报告
        故障报告                                    ↓ 失败
                                              自省分析（LLM 分析失败原因 + 新方案）
                                                    ↓
                                              retry_count += 1
                                                    ↓
                                              retry_count < 5？
                                                    ↓ 是
                                              更新飞书卡片（第 N 轮重试）
                                              [继续AI执行][转人工]
                                                    ↓ AI继续
                                              执行新方案 → 验证 → ...
                                                    ↓ 否（5轮耗尽）
                                              转人工升级
```

### 2.2 与现有流程的关系

新增两个 LangGraph 子图，复用现有节点：

| 节点 | 来源 | 复用/新增 |
|------|------|----------|
| `parse_alert` | 现有 | 复用 |
| `collect_context` | 现有 | 复用 |
| `diagnose` | 现有 `rca.py` | **改造**：Runbook 未命中时调用 Fallback Agent |
| `fallback_diagnose` | **新增** | LLM 自主生成 ActionPlan |
| `execute` | 现有 `executor.py` | **改造**：支持执行 AI 生成的命令 |
| `verify` | 现有 `verify.py` | **改造**：阈值从 AI 方案中动态提取 |
| `retry_analyze` | **新增** | 失败后 LLM 自省 + 新方案生成 |
| `generate_report` | 现有 | 复用（汇总含重试历史） |
| `escalate` | 现有 | 复用 |

---

## 三、核心组件设计

### 3.1 Fallback Agent（`agent/agents/fallback.py`）

当 `load_runbook()` 返回 `None` 时触发的兜底逻辑。

```python
# agent/agents/fallback.py 核心接口

async def generate_ai_action_plan(
    context: dict,      # 可观测数据（metrics/logs/pods/cmdb）
    alert: dict,        # 告警信息
    diagnosis: dict,    # LLM 根因诊断结果
) -> dict:
    """调用 LLM 自主生成处置方案

    Returns:
        {
            "action_plan": [
                {"risk_level": "中风险", "description": "...", "command": "kubectl scale ..."},
                ...
            ],
            "rollback": "kubectl rollout undo ...",
            "verification": {
                "metric": "cpu",          # 验证指标
                "operator": "<",           # < 或 >
                "threshold": 70.0,         # 阈值
                "description": "CPU 使用率降至 70% 以下"
            },
            "risk_assessment": {
                "level": "中风险",
                "score": 45,
                "warnings": ["AI 自主生成方案，需人工确认"],
                "allowed": True
            },
            "confidence": 0.75,
            "reasoning": "根据 CPU 和 QPS 数据判断，扩容可缓解..."  # LLM 的推理过程
        }
    """

async def analyze_failure_and_retry(
    incident_id: str,
    previous_plan: dict,       # 上一轮的方案
    execution_result: dict,    # 上一轮的执行结果（stdout/stderr/exit_code）
    verification_result: dict, # 上一轮的验证结果
    retry_count: int,          # 当前重试次数
    context: dict,             # 重新采集的可观测上下文
    alert: dict,
) -> dict:
    """执行失败后，LLM 自省原因并生成修正方案

    Returns:
        与 generate_ai_action_plan 相同结构，额外包含：
        {
            "retry_reasoning": "上一轮 kubectl scale 失败是因为...，本轮改为...",
            "previous_failure": "上一轮方案摘要 + 失败原因"
        }
    """
```

#### LLM Prompt 设计要点

```
你是 SRE 运维专家。当前告警未匹配到任何预置 Runbook，你需要自主分析并制定处置方案。

分析原则：
1. 基于提供的指标、日志、Pod 状态综合判断
2. 优先选择可逆操作（扩容、切流），避免不可逆操作（删库、删PV）
3. 命令必须严格限制在 kubectl 范围内，且是白名单允许的前缀
4. 给出每步的风险等级和理由
5. 指定验证条件（指标名 + 判断符 + 阈值）

白名单允许的命令前缀：
- kubectl scale deployment
- kubectl delete pod
- kubectl rollout undo deployment
- kubectl set resources deployment

输出 JSON 格式：
{
  "reasoning": "推理过程...",
  "steps": [
    {"risk_level": "低风险", "description": "...", "command": "kubectl scale ..."}
  ],
  "rollback": "...",
  "verification": {"metric": "cpu", "operator": "<", "threshold": 70.0, "description": "..."},
  "confidence": 0.75
}
```

### 3.2 飞书卡片设计

参照现有卡片风格：所有卡片 `wide_screen_mode: true`，内容用 `markdown` 块 + `hr` 分隔，底部 `action` 按钮 + `note` 脚注。

#### 3.2.1 AI 兜底诊断卡片（`templates/cards/fallback_diagnosis_card.json`）

```json
{
  "config": { "wide_screen_mode": true },
  "header": {
    "title": { "tag": "plain_text", "content": "{{alert_title}} - AI 自主诊断" },
    "template": "yellow"
  },
  "elements": [
    {
      "tag": "markdown",
      "content": "**⚠️ 未匹配到预置 Runbook，以下方案由 AI 自主分析生成，请仔细确认后操作**"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**根因判断：**\n{{root_cause}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**AI 推理过程：**\n{{ai_reasoning}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**AI 处置方案：**\n{{action_plan}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**验证条件：** {{verify_condition}}"
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
          "text": { "tag": "plain_text", "content": "AI 自动执行" },
          "type": "primary",
          "value": { "action": "approve_ai", "incident_id": "{{incident_id}}" }
        },
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "我自己来" },
          "type": "default",
          "value": { "action": "manual_fix", "incident_id": "{{incident_id}}" }
        },
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "拒绝" },
          "type": "danger",
          "value": { "action": "reject", "incident_id": "{{incident_id}}" }
        }
      ]
    },
    {
      "tag": "note",
      "elements": [
        { "tag": "plain_text", "content": "事件编号: {{incident_id}} | 状态: AI 方案待确认 | 耗时: {{duration}}" }
      ]
    }
  ]
}
```

#### 3.2.2 重试卡片（`templates/cards/retry_card.json`）

```json
{
  "config": { "wide_screen_mode": true },
  "header": {
    "title": { "tag": "plain_text", "content": "{{alert_title}} - 第 {{retry_round}}/5 轮重试" },
    "template": "orange"
  },
  "elements": [
    {
      "tag": "markdown",
      "content": "**⚠️ AI 第 {{retry_round}} 轮重试，上一轮处置未恢复，已自动调整方案**"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**上轮失败原因：**\n{{failure_reason}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**本轮自省分析：**\n{{retry_reasoning}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**本轮修正方案：**\n{{action_plan}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**验证条件：** {{verify_condition}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**历史摘要：**\n{{retry_history_summary}}"
    },
    { "tag": "hr" },
    {
      "tag": "action",
      "actions": [
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "继续 AI 执行" },
          "type": "primary",
          "value": { "action": "continue_retry", "incident_id": "{{incident_id}}", "round": {{retry_round}} }
        },
        {
          "tag": "button",
          "text": { "tag": "plain_text", "content": "转人工" },
          "type": "danger",
          "value": { "action": "stop_retry", "incident_id": "{{incident_id}}" }
        }
      ]
    },
    {
      "tag": "note",
      "elements": [
        { "tag": "plain_text", "content": "事件编号: {{incident_id}} | 状态: 第 {{retry_round}}/5 轮重试 | 已重试 {{retry_round}} 轮" }
      ]
    }
  ]
}
```

#### 3.2.3 文案风格说明

与现有卡片保持一致的规则：
- **标题**：`[级别] 服务 - 告警名` 格式（如 `[P1] order-service - DiskFull`），重试卡片追加 `- 第 N/5 轮重试`
- **颜色**：AI 兜底卡片 `yellow`（警示），重试卡片 `orange`（升级），区别于正常诊断的 `blue`
- **按钮文案**：动词短语祈使句，与现有"批准执行""拒绝""转人工"风格一致
- **脚注**：`事件编号: xxx | 状态: xxx | 耗时: xxx` 格式完全复用
- **内容块**：`markdown` tag，中文冒号，加粗 label（`**label：**`），与现有 diagnosis_card 完全一致

### 3.3 重试循环（Retry Loop）

#### 3.3.1 状态机

```
                    ┌──────────┐
                    │ execute  │
                    └────┬─────┘
                         │
              成功？─────┴─────失败
               │                 │
          ┌────▼────┐    ┌──────▼──────┐
          │ verify  │    │ retry_analyze│ (LLM 自省)
          └────┬────┘    └──────┬──────┘
               │                │
         成功？─┴─失败    retry_count < 5？
          │        │         │           │
     ┌────▼──┐ ┌──▼───┐  ┌──▼───┐  ┌───▼───┐
     │report │ │retry │  │飞书重试│  │escalate│
     └───────┘ │analyze│ │卡片   │  └────────┘
               └───────┘ └───────┘
```

#### 3.3.2 关键约束

| 约束 | 值 | 说明 |
|------|-----|------|
| 最大重试轮数 | 5 | 超过后强制升级人工 |
| 每轮超时 | 300s | 单轮执行+验证的总时间上限 |
| 命令白名单 | 不变 | AI 生成的命令仍需通过 `validate_command()` |
| 风险上限 | 中风险以下 | 高风险/极高风险 AI 方案自动拒绝执行 |
| 总时长上限 | 30min | 5 轮 × 6min，避免告警积压 |

#### 3.3.3 每轮重试发生的事

1. **重新采集上下文**：调用 Prometheus + Loki + K8s API 获取最新状态（上一轮命令可能已改变了系统状态）
2. **LLM 自省**：把上一轮的方案 + 执行结果（stdout/stderr/exit_code）+ 验证结果 + 最新上下文发给 LLM，要求分析失败原因并给出修正方案
3. **审计记录**：`audit_logs` 写一条 `retry_analysis` 记录，含 LLM 的自省结果
4. **飞书通知**：更新原诊断卡片为"第 N 轮重试"卡片
5. **等待审批**：用户点击"继续 AI 执行"或"转人工"
6. **执行新方案**：复用 `executor.py` 执行逻辑
7. **验证**：复用 `verify.py`，但阈值使用 AI 方案中指定的值

### 3.4 可观测性设计

#### 3.4.1 数据库变更

```sql
-- incidents 表新增字段
ALTER TABLE incidents ADD COLUMN retry_count INTEGER DEFAULT 0;
ALTER TABLE incidents ADD COLUMN retry_history JSONB;  -- 每轮方案+执行结果+失败原因
ALTER TABLE incidents ADD COLUMN ai_generated BOOLEAN DEFAULT FALSE;  -- 标记是否 AI 兜底方案
ALTER TABLE incidents ADD COLUMN ai_reasoning TEXT;  -- LLM 推理过程

-- executions 表新增字段
ALTER TABLE executions ADD COLUMN round INTEGER DEFAULT 1;  -- 重试轮次
ALTER TABLE executions ADD COLUMN ai_analysis TEXT;  -- 该轮 LLM 的分析文本
```

#### 3.4.2 retry_history JSONB 结构

```json
[
  {
    "round": 1,
    "timestamp": "2026-06-06T10:00:00Z",
    "plan": {"steps": [...], "verification": {...}},
    "execution": {"status": "failed", "stdout": "...", "stderr": "..."},
    "verification": {"recovered": false, "current": 85.0, "threshold": 70.0},
    "retry_analysis": "扩容后 CPU 仍然 85%，分析原因：可能单 Pod 热点..."
  },
  {
    "round": 2,
    ...
  }
]
```

#### 3.4.3 审计日志事件类型

| 事件 | action | 说明 |
|------|--------|------|
| AI 方案生成 | `ai_plan_generated` | Fallback Agent 首次生成方案 |
| 用户选择 AI 执行 | `ai_execution_approved` | 用户在飞书点"AI 自动执行" |
| 用户选择人工 | `manual_execution_chosen` | 用户点"我自己来" |
| 重试分析 | `retry_analysis` | LLM 自省 + 新方案生成 |
| 重试执行 | `retry_execution` | 第 N 轮执行 |
| 重试上限耗尽 | `retry_exhausted` | 5 轮后升级人工 |
| 命令被 AI 方案拦截 | `ai_command_blocked` | AI 生成的命令不在白名单 |

#### 3.4.4 Web Console 展示

- **Incident 详情页** 新增"AI 重试时间线"组件，树状展示每轮：分析 → 方案 → 执行 → 结果
- **Dashboard** 新增指标卡片：AI 兜底方案占比、AI 方案成功率、平均重试轮数

---

## 四、数据流

### 4.1 AI 兜底方案审批回调

```
Feishu 用户点击 "AI 自动执行"
  → POST /api/v1/approvals/callback
    → action = "approve_ai_fallback"
    → run_fallback_execution_workflow(incident_id)
      → _load_execution_state()  # 从 DB 恢复（含 ai_generated 标记）
      → fallback_execution_workflow.ainvoke(state)
        → execute → verify → [成功] report / [失败] retry_analyze
```

### 4.2 飞书回调扩展

按钮 `value` 结构与现有 format 一致（JSON 对象，含 `action` + `incident_id`）：

| 按钮 | value.action | 触发逻辑 |
|------|-------------|---------|
| "AI 自动执行" | `approve_ai` | 生成 execution state，进入 fallback 工作流 |
| "我自己来" | `manual_fix` | 发送纯文本命令列表到飞书群，工单状态置为 `manual_executing` |
| "拒绝" | `reject` | 复用现有 reject 逻辑 |
| "继续 AI 执行" | `continue_retry` | 从 DB 加载 retry_history，进入 retry_analyze → execute → verify 循环 |
| "转人工" | `stop_retry` | 终止重试循环，incident 置为 `escalated`，写 audit |

```python
# approvals.py 中 handle_card_action() 扩展
_ACTION_TO_STATUS = {
    "approve": "approved",          # 现有
    "reject": "rejected",           # 现有
    "escalate": "escalated",        # 现有
    "approve_ai": "ai_approved",    # 新增
    "manual_fix": "manual_approved",# 新增
    "continue_retry": "retry_continue",  # 新增
    "stop_retry": "retry_stop",     # 新增
}
```

`retry_card.json` 的按钮 value 多一个 `round` 字段用于定位重试轮次：

```json
{
  "action": "continue_retry",
  "incident_id": "INC-xxx",
  "round": 2
}
```

---

## 五、实施任务清单

### Phase A：AI 兜底核心（3 天）

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| A1 | Fallback Agent | `agent/agents/fallback.py`：LLM 自主方案生成 + 失败自省 | 核心 Agent |
| A2 | RCA 流程改造 | `rca.py` `_build_action_plan()` 未命中 Runbook 时调用 Fallback Agent | 兜底触发 |
| A3 | Fallback Prompt 调优 | 设计 system prompt + few-shot examples，确保 LLM 输出合法 kubectl 命令 | 可靠方案生成 |
| A4 | AI 方案风险校验 | 对 AI 生成的命令做白名单校验 + 风险等级二次确认，高风险方案自动拒绝 | 安全兜底 |

### Phase B：飞书交互（2 天）

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| B1 | 兜底诊断卡片 | `fallback_diagnosis_card.json`：含 AI 标记 + 3 个新按钮 | 新卡片模板 |
| B2 | 重试卡片 | `retry_card.json`：含第 N 轮标记 + 历史摘要 | 新卡片模板 |
| B3 | 审批回调扩展 | `approvals.py` 支持新 action 类型 + 路由到不同工作流 | 回调处理 |
| B4 | RCA 通知适配 | `rca.py` `_notify_diagnosis()` 区分正常卡片/AI 兜底卡片发送 | 通知路由 |

### Phase C：重试循环（2 天）

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| C1 | 重试工作流 | `build_retry_workflow()`：含 retry_analyze + execute + verify 循环 | LangGraph 图 |
| C2 | 上下文重采集 | `retry_analyze` 节点中重新调用 Prometheus/Loki/K8s 获取最新状态 | 状态刷新 |
| C3 | 验证阈值动态化 | `verify.py` 支持从 AI 方案中提取验证条件而非写死 THRESHOLDS | 通用验证 |
| C4 | 重试上限与降级 | retry_count >= 5 时强制 escalate，写 audit 记录 | 兜底保护 |

### Phase D：可观测性（1.5 天）

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| D1 | DB 迁移 | `incidents` 加 retry_count/retry_history/ai_generated/ai_reasoning 字段 | 新 schema |
| D2 | executions 扩展 | `executions` 加 round/ai_analysis 字段 | 执行可追溯 |
| D3 | 审计事件新增 | `audit.py` 支持 ai_plan_generated / retry_analysis / retry_exhausted 等事件 | 审计完整 |
| D4 | Web Console 时间线 | Incident 详情页展示 AI 重试树状历史 | 可观测 UI |

### Phase E：测试与联调（1 天）

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| E1 | 单元测试 | Fallback Agent + 重试循环的 pytest 用例 | 测试覆盖 |
| E2 | E2E 测试脚本 | 模拟未知告警→AI 兜底→执行→失败→重试→恢复 的完整链路 | E2E 覆盖 |
| E3 | 飞书卡片联调 | 在真实飞书群中验证新卡片样式和按钮交互 | 交互验证 |

---

## 六、风险与注意事项

| 风险 | 缓解措施 |
|------|----------|
| LLM 幻觉生成危险命令 | 1) 命令前缀白名单强制校验 2) 只允许中风险及以下自动执行 3) 高风险方案直接 escalate |
| LLM 陷入死循环（每次生成相同错误方案） | 1) 自省 prompt 明确要求 "本轮方案必须不同于前 N 轮" 2) 给 LLM 完整的历史上下文 |
| 重试耗时过长，告警积压 | 1) 单轮 300s 上限 2) 总 30min 上限 3) 重试期间该告警类型不重复创建 Incident |
| AI 方案验证条件不合理 | verify 节点对 AI 提供的阈值做合理性校验（如 CPU 阈值不能 < 0 或 > 100） |
| 用户不理解 AI 兜底方案的风险 | 飞书卡片黄色警告条 + 首次使用提示 + 每步标注 AI 生成标记 |

---

## 七、文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| **新增** | `agent/agents/fallback.py` | Fallback Agent（方案生成 + 失败自省） |
| **新增** | `agent/templates/cards/fallback_diagnosis_card.json` | AI 兜底诊断卡片 |
| **新增** | `agent/templates/cards/retry_card.json` | 重试轮次卡片 |
| **新增** | `agent/workflows/fallback_workflow.py` | AI 兜底执行 + 重试工作流 |
| **新增** | `migrations/002_fallback_retry.sql` | DB schema 变更 |
| **改造** | `agent/agents/rca.py` | `_build_action_plan()` 未命中时调用 Fallback Agent |
| **改造** | `agent/agents/rca.py` | `_notify_diagnosis()` 区分普通/AI 卡片 |
| **改造** | `agent/agents/verify.py` | 支持从 AI 方案动态提取验证阈值 |
| **改造** | `agent/agents/executor.py` | 增加 `round` 参数，支持记录重试轮次 |
| **改造** | `agent/api/v1/approvals.py` | 新增 action 类型路由 |
| **改造** | `agent/workflows/alert_workflow.py` | 新增 `build_fallback_workflow()` |
| **改造** | `agent/db/models.py` | Incident/Execution 新增字段 |
| **改造** | `agent/db/crud.py` | 新增字段的 CRUD 适配 |
| **改造** | `web/` | Incident 详情页新增重试时间线 |
