# Phase B: 飞书交互 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 兜底方案通过专用飞书卡片推送，用户可从卡片选择"AI 自动执行""我自己来""拒绝"三种处置方式，回调路由正确分发到不同处理分支。

**Architecture:** 新增两张飞书卡片模板 (`fallback_diagnosis_card.json` / `retry_card.json`)，改造 `_notify_diagnosis()` 按 `runbook.ai_generated` 标记自动选择正确模板，扩展 `handle_card_action()` action 映射 + `approval_callback()` 回调路由以支持 4 种新按钮行为。

**Tech Stack:** 飞书卡片 JSON 模板 + `render_card()` 字符串替换（复用现有机制），FastAPI BackgroundTasks 异步回调处理

**前置依赖:** Phase A 已完成 — `fallback.py` 输出 `ai_generated=True` 的 runbook dict，`risk_assessment` 含 `ai_generated` / `ai_reasoning` / `verification` / `ai_confidence` 字段。

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `agent/templates/cards/fallback_diagnosis_card.json` | **新增** | AI 兜底方案的诊断卡片模板 |
| `agent/templates/cards/retry_card.json` | **新增** | 重试轮次卡片模板（Phase C 消费） |
| `agent/agents/rca.py:399-463` | **修改** | `_notify_diagnosis()`：AI 方案路由到 fallback 卡片，传递 AI 专属占位符 |
| `agent/channels/feishu.py:92-106` | **修改** | `handle_card_action()`：新增 4 个 action → status 映射 |
| `agent/api/v1/approvals.py:14-19` | **修改** | `APPROVAL_DISPLAY`：新增 4 个状态的展示文案 |
| `agent/api/v1/approvals.py:111-188` | **修改** | `approval_callback()`：新增 action 分支（approve_ai→触发执行，manual_fix→仅通知） |
| `tests/test_templates.py` | **修改** | 新增 fallback/retry 卡片渲染测试 |
| `tests/test_approvals.py` | **修改** | 新增 action 映射 + 回调路由测试 |

---

### Task 1: 创建 AI 兜底诊断卡片模板

**Files:**
- Create: `agent/templates/cards/fallback_diagnosis_card.json`
- Modify: `tests/test_templates.py`

- [ ] **Step 1: 编写 `fallback_diagnosis_card.json`**

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
      "content": "**未匹配到预置 Runbook，以下方案由 AI 自主分析生成，请仔细确认后操作**"
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

模板说明（对照现有 `diagnosis_card.json` 差异）：
- **header.template**: `"yellow"` 固定 — 明确标识 AI 兜底区别于普通诊断
- **第一条 markdown**: 醒目的 AI 来源声明，替代普通卡片直接展示根因
- **新增 "AI 推理过程" 块**: 展示 LLM 为什么选择这个方案（来自 `runbook.ai_reasoning`）
- **"处置方案" 改为 "AI 处置方案"**: 再次强调来源
- **新增 "验证条件" 块**: 展示 AI 建议的验证指标 + 阈值 + 判断符
- **按钮变更**: `approve` → `approve_ai`，新增 `manual_fix`（与现有 `diagnosis_card.json` 三按钮位置对应）
- **note**: 状态文案改为 "AI 方案待确认"

- [ ] **Step 2: 编写卡片渲染测试**

在 `tests/test_templates.py` 末尾追加：

```python
class FallbackCardTemplateTest(TestCase):
    def test_render_fallback_card_shows_ai_warning_and_buttons(self):
        card = render_card(
            "fallback_diagnosis_card",
            alert_title="[P2] order-service - DiskPressure",
            root_cause="磁盘使用率过高导致服务写入失败",
            ai_reasoning="根因分析显示磁盘 I/O 等待时间增加，结合 Pod 日志中的 'No space left on device' 错误，判断为磁盘空间不足。建议清理临时文件并扩容 PVC。",
            action_plan="1. [低风险] 检查磁盘使用情况\n`kubectl get pods -n demo`\n2. [中风险] 清理临时文件\n`kubectl delete pod order-service-abc -n demo`",
            verify_condition="磁盘使用率 < 85%（指标: disk_usage, 操作符: <, 阈值: 85.0）",
            evidence_list="Pod 日志: No space left on device\n磁盘使用率: 92%",
            confidence="75",
            incident_id="INC-FALLBACK-01",
            duration="刚刚",
        )

        # 第一条元素是 AI 警告
        self.assertIn("AI 自主分析生成", card["elements"][0]["content"])

        # 包含 AI 推理过程
        self.assertIn("AI 推理过程", card["elements"][2]["content"])
        self.assertIn("磁盘 I/O", card["elements"][2]["content"])

        # 包含验证条件
        self.assertIn("验证条件", card["elements"][6]["content"])

        # 三个按钮: approve_ai, manual_fix, reject
        actions = card["elements"][10]["actions"]
        self.assertEqual(len(actions), 3)
        self.assertEqual(actions[0]["value"]["action"], "approve_ai")
        self.assertEqual(actions[0]["text"]["content"], "AI 自动执行")
        self.assertEqual(actions[1]["value"]["action"], "manual_fix")
        self.assertEqual(actions[1]["text"]["content"], "我自己来")
        self.assertEqual(actions[2]["value"]["action"], "reject")
        self.assertEqual(actions[2]["text"]["content"], "拒绝")

        # note 显示 AI 待确认状态
        self.assertIn("AI 方案待确认", card["elements"][11]["elements"][0]["content"])

    def test_fallback_card_button_value_contains_incident_id(self):
        card = render_card(
            "fallback_diagnosis_card",
            alert_title="[P2] test - TestAlert",
            root_cause="test",
            ai_reasoning="test reasoning",
            action_plan="1. [低风险] test",
            verify_condition="CPU < 70%",
            evidence_list="test",
            confidence="50",
            incident_id="INC-TEST-123",
            duration="1m",
        )

        for action_item in card["elements"][10]["actions"]:
            self.assertEqual(action_item["value"]["incident_id"], "INC-TEST-123")
```

- [ ] **Step 3: 运行测试确认卡片 JSON 可解析且渲染正确**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_templates.py::FallbackCardTemplateTest -v
```

期望：2 个测试 PASS。

- [ ] **Step 4: 提交**

```bash
git add agent/templates/cards/fallback_diagnosis_card.json tests/test_templates.py
git commit -m "feat: add Feishu fallback diagnosis card for AI-generated plans

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 创建重试卡片模板

**Files:**
- Create: `agent/templates/cards/retry_card.json`
- Modify: `tests/test_templates.py`

- [ ] **Step 1: 编写 `retry_card.json`**

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
      "content": "**AI 第 {{retry_round}} 轮重试，上一轮处置未恢复，已自动调整方案**"
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

模板说明：
- **header.template**: `"orange"` — 表示升级/重试状态，比 yellow 更紧迫
- **header.title**: 包含轮次信息 `第 N/5 轮`
- **内容块顺序**: 失败原因 → 自省分析 → 修正方案 → 验证条件 → 历史摘要，按重试决策信息优先级排列
- **按钮**: 仅两个 — "继续 AI 执行" (primary) / "转人工" (danger)，因为已经在重试中，"拒绝"无意义
- **"继续 AI 执行" 的 value**: 额外携带 `round` 字段用于后端定位当前轮次的 retry_history
- **note**: 展示重试轮次状态

- [ ] **Step 2: 编写重试卡片渲染测试**

在 `tests/test_templates.py` 末尾追加：

```python
class RetryCardTemplateTest(TestCase):
    def test_render_retry_card_shows_round_and_buttons(self):
        card = render_card(
            "retry_card",
            alert_title="[P2] order-service - DiskPressure",
            retry_round="2",
            failure_reason="第 1 轮 kubectl delete pod 执行成功但 CPU 未恢复，可能 Pod 重建后仍调度到同一热点节点",
            retry_reasoning="分析了 Prometheus 指标和 Node 状态，发现 node-3 的 CPU 已满载。本轮改为先驱逐 node-3 上的非关键 Pod，再扩容目标 Deployment 到其他节点。",
            action_plan="1. [中风险] 扩容 order-service 到其他节点\n`kubectl scale deployment order-service -n demo --replicas=4`",
            verify_condition="CPU 使用率 < 70%（指标: cpu, 操作符: <, 阈值: 70.0）",
            retry_history_summary="第 1 轮: 删除 Pod → CPU 仍 85% → 未恢复",
            incident_id="INC-RETRY-01",
        )

        # header 显示轮次
        self.assertIn("第 2/5 轮重试", card["header"]["title"]["content"])

        # 第一条是警告
        self.assertIn("第 2 轮重试", card["elements"][0]["content"])

        # 包含失败原因
        self.assertIn("上轮失败原因", card["elements"][2]["content"])
        self.assertIn("kubectl delete pod", card["elements"][2]["content"])

        # 包含自省分析
        self.assertIn("自省分析", card["elements"][4]["content"])
        self.assertIn("node-3", card["elements"][4]["content"])

        # 两个按钮
        actions = card["elements"][12]["actions"]
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["value"]["action"], "continue_retry")
        self.assertEqual(actions[0]["text"]["content"], "继续 AI 执行")
        self.assertEqual(actions[1]["value"]["action"], "stop_retry")
        self.assertEqual(actions[1]["text"]["content"], "转人工")

        # note 显示轮次
        self.assertIn("第 2/5 轮重试", card["elements"][13]["elements"][0]["content"])

    def test_retry_card_round3_shows_correct_count(self):
        card = render_card(
            "retry_card",
            alert_title="[P1] payment-service - ThreadPoolExhausted",
            retry_round="3",
            failure_reason="第 2 轮扩容未恢复，线程池仍满",
            retry_reasoning="检测到下游 inventory-service 响应超时导致线程堆积，本轮改为先重启下游服务再扩容",
            action_plan="1. [中风险] 重启 inventory-service\n`kubectl delete pod inventory-service-xyz -n demo`",
            verify_condition="错误率 < 2%",
            retry_history_summary="第 1 轮: 扩容 → 失败\n第 2 轮: 扩容 + 清理 → 失败",
            incident_id="INC-RETRY-02",
        )

        self.assertIn("第 3/5 轮重试", card["header"]["title"]["content"])
        actions = card["elements"][12]["actions"]
        self.assertEqual(actions[0]["value"]["round"], 3)
```

- [ ] **Step 3: 运行测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_templates.py::RetryCardTemplateTest -v
```

期望：2 个测试 PASS。

- [ ] **Step 4: 提交**

```bash
git add agent/templates/cards/retry_card.json tests/test_templates.py
git commit -m "feat: add Feishu retry card for AI retry rounds

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: 扩展飞书回调 action 映射

**Files:**
- Modify: `agent/channels/feishu.py:92-106` — `handle_card_action()` action 映射
- Modify: `agent/api/v1/approvals.py:14-19` — `APPROVAL_DISPLAY` 新增状态文案
- Modify: `agent/api/v1/approvals.py:111-188` — `approval_callback()` 新增分支
- Modify: `tests/test_approvals.py` — 测试新 action

- [ ] **Step 1: 扩展 `handle_card_action()` action 映射**

`agent/channels/feishu.py:92-106`，将 `status_map` 替换为：

```python
async def handle_card_action(action: str, incident_id: str) -> str:
    """Map a card button action to the incident approval status."""
    status_map = {
        "approve": "approved",
        "reject": "rejected",
        "escalate": "escalated",
        "approve_ai": "ai_approved",
        "manual_fix": "manual_executing",
        "continue_retry": "retry_continue",
        "stop_retry": "escalated",
    }
    status = status_map.get(action, "pending")
    logger.info(
        "飞书卡片动作: incident=%s, action=%s, status=%s",
        incident_id,
        action,
        status,
    )
    return status
```

- [ ] **Step 2: 扩展 `APPROVAL_DISPLAY` 状态文案**

`agent/api/v1/approvals.py:14-18`，将 `APPROVAL_DISPLAY` 替换为：

```python
APPROVAL_DISPLAY = {
    "approved": ("已批准执行", "green"),
    "rejected": ("已拒绝", "red"),
    "escalated": ("已转人工", "orange"),
    "pending": ("待审批", "blue"),
    "ai_approved": ("已批准 AI 自动执行", "green"),
    "manual_executing": ("已转人工执行，请手动执行方案中的命令", "blue"),
    "retry_continue": ("已批准继续重试", "orange"),
}
```

- [ ] **Step 3: 扩展 `approval_callback()` 执行触发条件**

`agent/api/v1/approvals.py:174`，将执行触发条件从仅匹配 `approved` 改为同时匹配 `approved` 和 `ai_approved`：

将：

```python
if approval_status == "approved":
    background_tasks.add_task(run_execution_workflow, incident_id, body)
```

改为：

```python
if approval_status in ("approved", "ai_approved"):
    background_tasks.add_task(run_execution_workflow, incident_id, body)
```

- [ ] **Step 4: 编写测试**

在 `tests/test_approvals.py` 中追加：

```python
class HandleCardActionExtendedTest(IsolatedAsyncioTestCase):
    async def test_new_actions_map_to_correct_statuses(self):
        self.assertEqual(await handle_card_action("approve_ai", "INC-1"), "ai_approved")
        self.assertEqual(await handle_card_action("manual_fix", "INC-1"), "manual_executing")
        self.assertEqual(await handle_card_action("continue_retry", "INC-1"), "retry_continue")
        self.assertEqual(await handle_card_action("stop_retry", "INC-1"), "escalated")

    async def test_unknown_action_defaults_to_pending(self):
        self.assertEqual(await handle_card_action("nonexistent_action", "INC-1"), "pending")


class ApprovalCallbackAiTest(IsolatedAsyncioTestCase):
    async def test_approve_ai_triggers_execution_workflow(self):
        background_tasks = BackgroundTasks()

        with (
            patch("agent.api.v1.approvals._update_incident_status", new=AsyncMock()),
            patch("agent.api.v1.approvals._update_feishu_card", new=AsyncMock()),
            patch("agent.api.v1.approvals._write_approval_audit", new=AsyncMock()),
            patch("agent.api.v1.approvals.run_execution_workflow", new=AsyncMock()) as run_workflow,
        ):
            response = await approval_callback(
                _JSONRequest({
                    "type": "card_action",
                    "action": {
                        "value": '{"action":"approve_ai","incident_id":"INC-AI-01"}',
                    },
                }),
                background_tasks,
            )
            for task in background_tasks.tasks:
                await task()

        self.assertEqual(response["approval_status"], "ai_approved")
        run_workflow.assert_awaited_once()

    async def test_manual_fix_does_not_trigger_execution(self):
        background_tasks = BackgroundTasks()

        with (
            patch("agent.api.v1.approvals._update_incident_status", new=AsyncMock()),
            patch("agent.api.v1.approvals._update_feishu_card", new=AsyncMock()),
            patch("agent.api.v1.approvals._write_approval_audit", new=AsyncMock()),
            patch("agent.api.v1.approvals.run_execution_workflow", new=AsyncMock()) as run_workflow,
        ):
            response = await approval_callback(
                _JSONRequest({
                    "type": "card_action",
                    "action": {
                        "value": '{"action":"manual_fix","incident_id":"INC-MANUAL-01"}',
                    },
                }),
                background_tasks,
            )
            for task in background_tasks.tasks:
                await task()

        self.assertEqual(response["approval_status"], "manual_executing")
        run_workflow.assert_not_awaited()
```

- [ ] **Step 5: 运行全部 approvals 测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_approvals.py -v
```

期望：所有测试（含已有 5 个 + 新增 4 个）PASS。

- [ ] **Step 6: 提交**

```bash
git add agent/channels/feishu.py agent/api/v1/approvals.py tests/test_approvals.py
git commit -m "feat: extend Feishu callback for AI fallback approve/manual/retry actions

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: RCA 诊断通知适配 AI 卡片路由

**Files:**
- Modify: `agent/agents/rca.py:399-463` — `_notify_diagnosis()` 按 `runbook.ai_generated` 选择卡片模板
- Modify: `agent/agents/rca.py:467-482` — `_format_action_plan()` 已实现（Phase A 已完成），本次无需改动
- Modify: `agent/agents/rca.py:485-490` — `_format_risk_warnings()` 已实现，本次无需改动
- Modify: `tests/test_phase2_workflow.py` — 覆盖 AI 卡片路由

- [ ] **Step 1: 改造 `_notify_diagnosis()` 支持卡片路由**

`agent/agents/rca.py:399-463`，将 `_notify_diagnosis()` 替换为：

```python
async def _notify_diagnosis(
    incident_id: str,
    diagnosis: dict,
    alert: dict,
    runbook: dict | None = None,
    risk_assessment: dict | None = None,
):
    """推送诊断结果卡片到飞书群。

    AI 兜底方案使用 fallback_diagnosis_card 模板（黄色警告 + 推理过程 + 三按钮），
    预置 Runbook 方案继续使用 diagnosis_card 模板。
    """
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
        action_plan = _format_action_plan(runbook)
        risk_summary = risk_assessment or {}
        is_ai = bool(runbook and runbook.get("ai_generated"))
        template_name = "fallback_diagnosis_card" if is_ai else "diagnosis_card"
        logger.info(
            "准备发送诊断卡片: incident=%s, service=%s, runbook=%s, template=%s, risk=%s",
            incident_id,
            service,
            runbook.get("name") if runbook else "-",
            template_name,
            risk_summary.get("level", "未评估"),
        )

        if is_ai:
            # AI 兜底卡片：传递推理过程和验证条件
            verification = runbook.get("verification", {})
            verify_text = (
                f"{verification.get('description', 'N/A')} "
                f"（指标: {verification.get('metric', 'N/A')}, "
                f"操作符: {verification.get('operator', 'N/A')}, "
                f"阈值: {verification.get('threshold', 'N/A')}）"
            )
            card = render_card(
                template_name,
                alert_title=f"[{severity}] {service} - {alert_name}",
                root_cause=diagnosis.get("root_cause", ""),
                ai_reasoning=runbook.get("ai_reasoning", "AI 未提供推理过程"),
                action_plan=action_plan,
                verify_condition=verify_text,
                evidence_list="\n".join(diagnosis.get("evidence", [])),
                confidence=f"{diagnosis.get('confidence', 0) * 100:.0f}",
                incident_id=incident_id,
                duration="刚刚",
            )
        else:
            card = render_card(
                template_name,
                alert_title=f"[{severity}] {service} - {alert_name}",
                severity_color=severity_color_map.get(severity, "blue"),
                root_cause=diagnosis.get("root_cause", ""),
                action_plan=action_plan,
                risk_level=risk_summary.get("level", "未评估"),
                risk_score=str(risk_summary.get("score", 0)),
                risk_warnings=_format_risk_warnings(risk_summary),
                evidence_list="\n".join(diagnosis.get("evidence", [])),
                confidence=f"{diagnosis.get('confidence', 0) * 100:.0f}",
                incident_id=incident_id,
                status="待审批" if runbook else "待确认",
                duration="刚刚",
            )

        chat_id = await get_service_chat_id(service)
        if chat_id:
            result = await send_card_to_chat(chat_id, card)
            logger.info(
                "诊断通知已发送: chat_id=%s, incident=%s, template=%s, code=%s",
                chat_id,
                incident_id,
                template_name,
                result.get("code"),
            )
        else:
            logger.warning(
                "服务未配置 chat_id，跳过诊断通知: service=%s, incident=%s",
                service,
                incident_id,
            )
    except Exception as e:
        logger.error("诊断通知发送失败: incident=%s, error=%s", incident_id, e)
```

- [ ] **Step 2: 运行已有测试确认无回归**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/test_phase2_workflow.py tests/test_templates.py -v
```

期望：所有已有测试 PASS（`_notify_diagnosis` 在 test_phase2_workflow 中被 mock，不受影响）。

- [ ] **Step 3: 运行全部测试**

```bash
cd /Users/zhouqiantalaogong/PycharmProjects/ops-ai-agent && python -m pytest tests/ -v --ignore=tests/e2e_phase1.sh --ignore=tests/e2e_phase2.sh --ignore=tests/e2e_phase3.sh 2>&1 | tail -40
```

期望：所有测试 PASS。

- [ ] **Step 4: 提交**

```bash
git add agent/agents/rca.py
git commit -m "feat: route AI fallback plans to dedicated Feishu card template

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 依赖关系

```
Task 1 (fallback_diagnosis_card.json) ─────────────────────┐
Task 2 (retry_card.json)                                   │
Task 3 (callback extension) ───────────────────────────────┤
                                                            ↓
                                              Task 4 (RCA 通知路由)
```

Task 1-3 相互独立，可并行执行。Task 4 依赖 Task 1（卡片模板存在）和 Task 3（action 映射就绪，但只影响 approve_ai→执行触发的路径，不影响 Task 4 的卡片发送逻辑）。

---

## 自检清单

- [x] Spec 覆盖：B1 对应 Task 1，B2 对应 Task 2，B3 对应 Task 3，B4 对应 Task 4
- [x] 无占位符：所有 JSON、Python 代码、测试断言完整写出
- [x] 类型一致性：render_card 参数名与模板占位符一一对应（`{{retry_round}}` ← `retry_round="2"`）
- [x] 按钮 value 兼容性：`fallback_diagnosis_card.json` 按钮 value 含 `incident_id`（字符串替换后仍为合法 JSON），`retry_card.json` 按钮 value 含 `round`（整数字面量，JSON 中合法）
- [x] 降级安全：`is_ai = bool(runbook and runbook.get("ai_generated"))` 确保 runbook 为 None 时不会误入 AI 卡片路径
