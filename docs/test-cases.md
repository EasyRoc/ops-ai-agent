# Ops AI Agent 测试用例文档

> 适用版本：当前主干已覆盖 AI 兜底完整链路，包含告警接入、诊断、Runbook、AI 兜底方案、风险评估、飞书确认、自动执行、失败重试、恢复验证、审计时间线和故障报告。
>
> 建议读法：先执行「冒烟测试」和「端到端验收」，如果失败，再按对应模块用例定位问题。

## 1. 测试目标

本测试用例用于验证 Ops AI Agent 在本地部署环境中的核心能力：

- 本地环境可以启动、重启、停止和查看状态。
- Prometheus、Alertmanager、Grafana、Loki、Promtail 和 Demo 服务可用。
- Agent 可以接收 Alertmanager Webhook 并创建 Incident。
- Agent 可以完成根因诊断、Runbook 匹配或 AI 兜底方案生成、风险评估和待确认状态写入。
- 飞书卡片回调可以更新审批状态，并把原卡片替换为无按钮的结果卡片。
- 确认通过后可以触发白名单自动执行、恢复验证和故障报告生成。
- AI 方案执行后未恢复时，可以生成修正方案并进入最多 5 轮确认式重试。
- 重复告警在去重窗口内不会重复诊断、重复发送卡片或重复创建新 Incident。
- Web Console 可以查看 Incident、执行记录、重试时间线、审计日志和故障报告。
- RBAC 和安全边界生效，普通角色不能直接触发执行。

## 2. 测试范围

| 模块 | 是否覆盖 | 说明 |
|------|----------|------|
| 本地启动脚本 `ops.sh` | 覆盖 | bootstrap、start、restart、stop、status、logs、demo、test、clean |
| Agent API | 覆盖 | health、alerts、incidents、approval、executions、reports |
| 诊断工作流 | 覆盖 | parse、context、rca、runbook、fallback、risk、approval |
| 执行工作流 | 覆盖 | execute、verify、report |
| AI 重试工作流 | 覆盖 | retry_execute、retry_verify、retry_analyze、retry_exhausted |
| 飞书回调 | 覆盖 | challenge、approve、approve_ai、manual_fix、continue_retry、stop_retry、reject、escalate、卡片更新 |
| 审计可观测 | 覆盖 | audit API、AI 推理、重试历史、执行轮次 |
| 可观测栈 | 覆盖 | Prometheus、Alertmanager、Grafana、Loki、Promtail |
| Web Console | 覆盖 | 首页、详情页、执行页、报告页 |
| Demo 服务 | 覆盖 | order、payment、inventory、frontend |
| Windows 原生环境 | 不覆盖 | 当前仅推荐 macOS、Linux、Windows + WSL2 |

## 3. 测试环境要求

推荐环境：

| 环境 | 要求 |
|------|------|
| macOS | Docker Desktop、Homebrew、kind、kubectl、helm、maven、python3、curl |
| Linux | Docker、Docker Compose v2、kind、kubectl、helm、maven、python3、curl |
| Windows | 使用 WSL2 Ubuntu，不建议在原生 CMD 或 PowerShell 中运行 |

启动前确认：

```bash
docker info
docker compose version
kubectl version --client
kind version
helm version
mvn -v
python3 --version
```

项目配置文件：

```bash
cp .env.example .env
```

可选配置：

```bash
DEEPSEEK_API_KEY=sk-xxx
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
SERVICE_CHAT_IDS='{"order-service":"oc_xxx","payment-service":"oc_xxx"}'
```

说明：

- DeepSeek 未配置时，根因分析会走规则兜底；AI 兜底方案和失败自省重试需要可用 LLM。
- 飞书未配置时，诊断、数据库保存、Web Console、API 和 E2E 仍可测试。
- 飞书卡片按钮需要公网 HTTPS 回调地址，详见 `docs/feishu-card-callback.md`。

## 4. 测试数据

### 4.1 Demo 服务

| 服务 | Kubernetes Deployment | 默认副本数 | 说明 |
|------|------------------------|------------|------|
| order-service | `order-service` | 2 | 主要用于 CPU 故障、Runbook 自动扩容和 AI 兜底 E2E |
| payment-service | `payment-service` | 2 | 支付样例服务 |
| inventory-service | `inventory-service` | 2 | 库存样例服务 |
| frontend-service | `frontend-service` | 2 | 前端样例服务 |

### 4.2 常用告警名称

| 告警名 | Runbook | 说明 |
|--------|---------|------|
| `HighCPUUsage` | `cpu_high.md` | CPU 使用率过高 |
| `HighErrorRate` | `error_rate.md` | 错误率过高 |
| `HighLatency` | `latency_high.md` | 延迟过高 |
| `OOMKilled` | `oom.md` | 容器 OOM |
| `ThreadPoolExhausted` / `DiskPressure` | `ai_fallback` | 未知告警，验证 AI 兜底方案 |

### 4.3 角色头

执行类 API 使用简单 RBAC：

| Header | 说明 |
|--------|------|
| `X-User-Role: viewer` | 只读角色，不能触发执行 |
| `X-User-Role: operator` | 运维角色，可以触发执行 |
| `X-User-Role: admin` | 管理角色，可以触发执行 |

## 5. 快速验收顺序

新用户或回归测试建议按这个顺序执行：

1. 执行 `./ops.sh bootstrap` 或 `./ops.sh start`。
2. 执行 `./ops.sh status`，确认所有组件正常。
3. 执行 Python 单元测试。
4. 打开 Web Console、Grafana、Prometheus。
5. 执行 `tests/e2e_phase2.sh`。
6. 执行 `tests/e2e_phase3.sh`。
7. 执行 `tests/e2e_retry_loop.sh`。
8. 执行 `tests/e2e_full_pipeline.sh`。
9. 检查重复告警去重。
10. 如果配置了飞书，手动点击飞书卡片按钮验证回调、卡片更新和重试卡片。

## 6. 冒烟测试

### TC-SMOKE-001 本地环境首次启动

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 冒烟测试 |
| 前置条件 | Docker 已启动，依赖命令已安装，项目根目录存在 `.env` |
| 操作步骤 | 在项目根目录执行 `./ops.sh bootstrap` |
| 预期结果 | PostgreSQL、Redis、Kind、Prometheus、Alertmanager、Grafana、Loki、Promtail、Demo 服务和 Agent 启动成功 |
| 验证命令 | `./ops.sh status` |
| 清理方式 | 无需清理，继续后续测试 |

### TC-SMOKE-002 已有环境启动

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 冒烟测试 |
| 前置条件 | 已经执行过 `bootstrap`，Kind 集群和监控组件存在 |
| 操作步骤 | 执行 `./ops.sh start` |
| 预期结果 | 后台端口转发和 Agent 正常启动 |
| 验证命令 | `curl -s http://localhost:8000/health` |
| 预期响应 | `{"status":"ok"}` |

### TC-SMOKE-003 状态检查

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 冒烟测试 |
| 前置条件 | 环境已启动 |
| 操作步骤 | 执行 `./ops.sh status` |
| 预期结果 | Agent、Prometheus、Alertmanager、Loki、Grafana、order-service 均显示正常 |
| 重点检查 | Demo Deployment 均为 `2/2`，Prometheus demo targets 显示全部 UP |

### TC-SMOKE-004 重启托管进程

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 类型 | 运维脚本测试 |
| 前置条件 | 环境已启动 |
| 操作步骤 | 执行 `./ops.sh restart` |
| 预期结果 | Agent 和端口转发进程重启成功，服务端点恢复正常 |
| 验证命令 | `./ops.sh status` |

### TC-SMOKE-005 停止环境

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 类型 | 运维脚本测试 |
| 前置条件 | 环境已启动 |
| 操作步骤 | 执行 `./ops.sh stop` |
| 预期结果 | Agent、端口转发和 Docker Compose 服务停止，Kind 集群保留 |
| 恢复方式 | 执行 `./ops.sh start` |

## 7. 自动化测试用例

### TC-AUTO-001 Python 单元测试全量回归

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 自动化测试 |
| 前置条件 | Python 依赖已安装，推荐已执行 `./ops.sh bootstrap` |
| 操作步骤 | 执行 `.venv/bin/python -m unittest discover -s tests -p 'test*.py' -v` |
| 预期结果 | 所有测试通过 |
| 覆盖范围 | 告警工作流、审批、执行器、验证、报告、风险、Runbook、AI 兜底、重试工作流、Web Console、飞书封装等 |

### TC-AUTO-002 Shell 脚本语法检查

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 类型 | 自动化测试 |
| 操作步骤 | 执行 `bash -n ops.sh && for f in tests/e2e_phase1.sh tests/e2e_ai_fallback.sh tests/e2e_phase2.sh tests/e2e_phase3.sh tests/e2e_retry_loop.sh tests/e2e_full_pipeline.sh; do bash -n "$f"; done` |
| 预期结果 | 命令无输出且退出码为 0 |

### TC-AUTO-003 本地脚本测试

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 类型 | 自动化测试 |
| 操作步骤 | 执行 `bash scripts/tests/test_ops.sh` |
| 预期结果 | `ops.sh` 相关基础行为测试通过 |

## 8. 可观测栈测试用例

### TC-OBS-001 Prometheus 健康检查

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 前置条件 | 环境已启动 |
| 操作步骤 | 执行 `curl -s http://localhost:9090/-/healthy` |
| 预期结果 | 返回 `Prometheus Server is Healthy.` |

### TC-OBS-002 Prometheus Demo Targets

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 操作步骤 | 打开 `http://localhost:9090/targets`，或执行 `./ops.sh status` |
| 预期结果 | demo namespace 下服务 targets 为 UP |
| 常见失败原因 | Demo 服务未部署、ServiceMonitor 未生效、端口转发未启动 |

### TC-OBS-003 Prometheus 告警规则

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 打开 `http://localhost:9090/alerts` |
| 预期结果 | 可以看到 Demo 服务相关告警规则 |
| 说明 | 无故障时告警通常不是 firing，这是正常的 |

### TC-OBS-004 Alertmanager 健康检查

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 执行 `curl -s http://localhost:9093/-/healthy` |
| 预期结果 | 返回健康状态 |

### TC-OBS-005 Loki 健康检查

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 执行 `curl -s http://localhost:3100/ready` |
| 预期结果 | 返回 `ready` |

### TC-OBS-006 Grafana 可访问

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 打开 `http://localhost:30030` |
| 预期结果 | 可以进入 Grafana 登录页 |
| 默认账号 | `admin / admin123` |

### TC-OBS-007 Demo 服务监控看板

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 登录 Grafana 后打开 `http://localhost:30030/d/demo-services/demo-services-overview` |
| 预期结果 | 可以看到 demo 服务 QPS、错误率、延迟、CPU 等面板 |
| 注意事项 | 如果只有 Kubernetes mixin 看板，请确认 Demo Dashboard 是否已 provision 或重新执行 `./ops.sh restart` |

## 9. Agent API 测试用例

### TC-API-001 Agent 健康检查

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 操作步骤 | 执行 `curl -s http://localhost:8000/health` |
| 预期结果 | 返回 `{"status":"ok"}` |

### TC-API-002 查询 Incident 列表

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 操作步骤 | 执行 `curl -s "http://localhost:8000/api/v1/incidents?limit=5"` |
| 预期结果 | 返回 JSON，包含 `total` 和 `incidents` 字段 |

### TC-API-003 查询 Incident 详情

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 前置条件 | 已存在一个 Incident |
| 操作步骤 | 执行 `curl -s http://localhost:8000/api/v1/incidents/INC-xxxx` |
| 预期结果 | 返回服务名、环境、级别、状态、根因、证据、Runbook、风险评估、审批状态等字段 |

### TC-API-004 查询审批状态

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已存在一个 Incident |
| 操作步骤 | 执行 `curl -s http://localhost:8000/api/v1/incidents/INC-xxxx/approval` |
| 预期结果 | 返回 `incident_id`、`status`、`approval_status` |

### TC-API-005 查询执行记录列表

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 执行 `curl -s "http://localhost:8000/api/v1/executions?limit=10"` |
| 预期结果 | 返回 `total` 和 `executions` |

### TC-API-006 查询单个 Incident 执行记录

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已完成 Runbook 自动执行或 AI 自动执行 |
| 操作步骤 | 执行 `curl -s http://localhost:8000/api/v1/incidents/INC-xxxx/executions` |
| 预期结果 | 至少包含一条执行记录，字段包括 `action`、`operator`、`status`、`result`，AI 重试场景还包含轮次信息 |

### TC-API-007 查询故障报告列表

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 执行 `curl -s "http://localhost:8000/api/v1/reports?limit=10"` |
| 预期结果 | 返回 `total` 和 `reports` |

### TC-API-008 查询 Markdown 故障报告

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已生成故障报告 |
| 操作步骤 | 执行 `curl -s "http://localhost:8000/api/v1/reports/INC-xxxx?format=markdown"` |
| 预期结果 | 返回 Markdown 内容，包含 `## 根因`、`## 执行结果`、`## 验证结果` |

## 10. 告警接入和诊断测试用例

### TC-ALERT-001 手动发送 HighCPUUsage 告警

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | API 功能测试 |
| 前置条件 | Agent 已启动 |
| 操作步骤 | 执行下面命令发送模拟告警 |
| 预期结果 | `/api/v1/alerts` 返回 `{"status":"accepted"}`，稍后创建新 Incident |

```bash
FINGERPRINT="manual-cpu-$(date +%s)"
curl -s -X POST http://localhost:8000/api/v1/alerts \
  -H "Content-Type: application/json" \
  -d "{
    \"receiver\": \"manual-test\",
    \"alerts\": [{
      \"status\": \"firing\",
      \"labels\": {
        \"alertname\": \"HighCPUUsage\",
        \"service\": \"order-service\",
        \"env\": \"prod\",
        \"severity\": \"P1\"
      },
      \"annotations\": {
        \"summary\": \"Manual CPU alert\",
        \"value\": \"CPU > 90%\"
      },
      \"startsAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"fingerprint\": \"${FINGERPRINT}\"
    }]
  }"
```

### TC-ALERT-002 验证诊断字段

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 前置条件 | 已执行 TC-ALERT-001 |
| 操作步骤 | 查询最新 Incident：`curl -s "http://localhost:8000/api/v1/incidents?limit=1"` |
| 预期结果 | 最新 Incident 包含 `root_cause`、`confidence`、`evidence`，状态进入 `diagnosed` 或 `pending_approval` |

### TC-ALERT-003 验证 Runbook 和风险评估

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 前置条件 | 已执行 TC-ALERT-001 |
| 操作步骤 | 查询最新 Incident 详情 |
| 预期结果 | `runbook_name` 为 `cpu_high.md`，`action_plan` 不为空，`risk_assessment.level` 不为空，`approval_status` 为 `pending` |

### TC-ALERT-004 验证不同告警匹配不同 Runbook

| 字段 | 内容 |
|------|------|
| 优先级 | P2 |
| 操作步骤 | 分别发送 `HighErrorRate`、`HighLatency`、`OOMKilled` 模拟告警 |
| 预期结果 | 分别匹配 `error_rate.md`、`latency_high.md`、`oom.md` |
| 注意事项 | 每次发送告警时使用不同 `fingerprint` |

## 11. 告警去重测试用例

### TC-DEDUP-001 相同 fingerprint 不重复创建和诊断

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 回归测试 |
| 前置条件 | Agent、Redis 已启动 |
| 操作步骤 | 使用同一个 `fingerprint` 连续发送两次相同告警 |
| 预期结果 | 第一次创建 Incident，第二次命中去重，不创建新 Incident，不继续诊断，不重复发送飞书卡片 |
| 默认窗口 | `ALERT_DEDUP_WINDOW=300` 秒 |

测试命令：

```bash
FINGERPRINT="dedup-$(date +%s)"
PAYLOAD="{
  \"receiver\": \"dedup-test\",
  \"alerts\": [{
    \"status\": \"firing\",
    \"labels\": {
      \"alertname\": \"HighCPUUsage\",
      \"service\": \"order-service\",
      \"env\": \"prod\",
      \"severity\": \"P1\"
    },
    \"annotations\": {
      \"summary\": \"Dedup CPU alert\",
      \"value\": \"CPU > 90%\"
    },
    \"startsAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
    \"fingerprint\": \"${FINGERPRINT}\"
  }]
}"

BEFORE=$(curl -s "http://localhost:8000/api/v1/incidents?limit=100" | python3 -c 'import json,sys; print(json.load(sys.stdin)["total"])')
curl -s -X POST http://localhost:8000/api/v1/alerts -H "Content-Type: application/json" -d "$PAYLOAD" >/dev/null
sleep 5
MID=$(curl -s "http://localhost:8000/api/v1/incidents?limit=100" | python3 -c 'import json,sys; print(json.load(sys.stdin)["total"])')
curl -s -X POST http://localhost:8000/api/v1/alerts -H "Content-Type: application/json" -d "$PAYLOAD" >/dev/null
sleep 5
AFTER=$(curl -s "http://localhost:8000/api/v1/incidents?limit=100" | python3 -c 'import json,sys; print(json.load(sys.stdin)["total"])')
echo "before=${BEFORE}, after_first=${MID}, after_second=${AFTER}"
```

判定标准：

- `after_first` 应比 `before` 大 1。
- `after_second` 应等于 `after_first`。
- Agent 日志应出现 `重复告警已跳过`。
- 第二次请求后不应再出现新一轮 `收集上下文`、`诊断完成`、`发送飞书卡片` 日志。

日志查看：

```bash
./ops.sh logs agent
```

## 12. 飞书审批回调测试用例

### TC-FEISHU-001 回调 challenge 校验

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 前置条件 | Agent 已启动 |
| 操作步骤 | 执行下面命令 |
| 预期结果 | 返回 `{"challenge":"ping"}` |

```bash
curl -s -X POST http://localhost:8000/api/v1/approvals/callback \
  -H "Content-Type: application/json" \
  -d '{"challenge":"ping"}'
```

### TC-FEISHU-002 模拟批准执行

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 前置条件 | 已存在 `approval_status=pending` 的 Incident |
| 操作步骤 | 执行下面命令，把 `INC-xxxx` 替换为真实 ID |
| 预期结果 | API 返回 `approval_status=approved`，数据库中 Incident 审批状态变为 `approved`，后台触发普通执行工作流 |

```bash
curl -s -X POST http://localhost:8000/api/v1/approvals/callback \
  -H "Content-Type: application/json" \
  -d '{
    "type": "card_action",
    "operator": {"name": "manual-tester"},
    "action": {
      "value": {
        "action": "approve",
        "incident_id": "INC-xxxx"
      }
    }
  }'
```

### TC-FEISHU-003 模拟拒绝

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已存在 `approval_status=pending` 的 Incident |
| 操作步骤 | 将 `action` 改为 `reject` 调用回调接口 |
| 预期结果 | `approval_status` 更新为 `rejected`，不会触发自动执行 |

### TC-FEISHU-004 模拟转人工

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已存在 `approval_status=pending` 的 Incident |
| 操作步骤 | 将 `action` 改为 `escalate` 调用回调接口 |
| 预期结果 | `approval_status` 更新为 `escalated`，不会触发自动执行 |

### TC-FEISHU-005 飞书真实卡片按钮点击

| 字段 | 内容 |
|------|------|
| 优先级 | P0，配置飞书时执行 |
| 前置条件 | `.env` 已配置飞书应用和 `SERVICE_CHAT_IDS`，飞书开放平台已配置卡片回调公网 HTTPS 地址 |
| 操作步骤 | 触发一条告警，等待飞书群收到诊断卡片，点击「批准执行」 |
| 预期结果 | Incident 审批状态更新为 `approved`，飞书原卡片按钮被替换为审批结果卡片 |
| 注意事项 | 只有真实飞书卡片回调携带 message id 且应用凭证可用时，项目才有条件更新原卡片；直接用 curl 模拟回调只能验证数据库状态和后台执行 |

### TC-FEISHU-006 公网回调地址变化处理

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 场景 | 使用 Wi-Fi、cloudflared 或 ngrok 本地联调 |
| 操作步骤 | 重启隧道工具后，检查公网域名是否变化 |
| 预期结果 | 如果公网域名变化，需要在飞书开放平台重新配置回调地址 |
| 建议 | 本地开发优先使用「开发者服务器」方式，地址填写 `https://公网域名/api/v1/approvals/callback` |

## 13. Phase 1 端到端测试

### TC-E2E-001 CPU 故障注入到诊断完成

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 端到端 |
| 前置条件 | 环境已启动，order-service 端口转发可用 |
| 操作步骤 | 执行 `./ops.sh test cpu`，或执行 `./ops.sh test` 跑默认全量 E2E |
| 预期结果 | CPU 场景会注入故障，Prometheus 触发告警，Agent 创建 Incident 并完成诊断；默认全量还会继续执行 AI 兜底诊断场景 |
| 清理方式 | 脚本退出时会自动调用 `/fault/reset` |

也可以直接执行：

```bash
tests/e2e_phase1.sh
```

### TC-E2E-001B AI 兜底诊断

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 端到端 |
| 前置条件 | Agent、PostgreSQL、Redis、Prometheus、Kubernetes Demo 和 LLM 配置均可用 |
| 操作步骤 | 执行 `./ops.sh test ai` |
| 预期结果 | 脚本发送未知告警，Agent 生成 `ai_fallback` 方案，`risk_assessment.ai_generated=true`，审批状态先进入 `pending`，模拟「我自己来」后进入 `manual_executing` |
| 排查提示 | 如果未生成 `ai_fallback`，优先检查 LLM 配置、Agent 日志中的 `Fallback Agent` 和 `AI 兜底` 关键字 |

## 14. Phase 2 端到端测试

### TC-E2E-002 Runbook、风险评估和审批状态

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 端到端 |
| 前置条件 | Agent 已启动 |
| 操作步骤 | 执行 `tests/e2e_phase2.sh` |
| 预期结果 | 创建新 Incident，匹配 `cpu_high.md`，生成 action_plan 和 risk_assessment，状态进入 `pending`，模拟批准后变为 `approved` |
| 额外验证 | Web Console 首页和 Incident 详情页可访问 |

## 15. 自动执行与报告端到端测试

### TC-E2E-003 审批后自动执行、验证和报告

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 端到端 |
| 前置条件 | Agent、PostgreSQL、Prometheus、Kubernetes Demo 均在线 |
| 操作步骤 | 执行 `tests/e2e_phase3.sh` |
| 预期结果 | 创建新 Incident，模拟飞书批准，产生执行记录，执行状态为 `success`，生成故障报告 |
| 报告校验 | 报告内容包含 `## 执行结果` 和 `## 验证结果` |
| 页面校验 | `/executions.html` 和 `/reports.html` 可访问 |

注意：

- 当前 CPU Runbook 的自动执行示例会把 `order-service` 扩容到 4 个副本。
- 测试后如需恢复默认副本数，执行：

```bash
kubectl scale deployment order-service -n demo --replicas=2 --context kind-ops-agent
```

### TC-E2E-004 AI 兜底重试循环

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 端到端 |
| 前置条件 | Agent、PostgreSQL、Redis、Prometheus、Kubernetes Demo 和 LLM 配置均可用 |
| 操作步骤 | 执行 `tests/e2e_retry_loop.sh` |
| 预期结果 | 创建未知告警 Incident，生成 `ai_fallback`，模拟 `approve_ai` 后产生执行或重试记录，审计日志包含 `ai_plan_generated`，Web Console 展示重试和审计时间线 |

### TC-E2E-005 AI 兜底完整链路

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 端到端 |
| 前置条件 | 完整本地环境已启动，LLM 配置可用 |
| 操作步骤 | 执行 `tests/e2e_full_pipeline.sh` |
| 预期结果 | 覆盖未知告警、AI 方案、飞书批准回调、执行记录、审计事件、Web Console 时间线；环境恢复时可生成故障报告 |

## 16. 自动执行和安全边界测试用例

### TC-EXEC-001 未审批 Incident 不能手动执行

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 前置条件 | 已存在 `approval_status=pending` 的 Incident |
| 操作步骤 | 执行 `curl -i -X POST http://localhost:8000/api/v1/incidents/INC-xxxx/execute -H "X-User-Role: operator"` |
| 预期结果 | 返回 HTTP 409，提示 Incident 尚未批准 |

### TC-EXEC-002 viewer 角色不能触发执行

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 前置条件 | 已存在 `approval_status=approved` 的 Incident |
| 操作步骤 | 执行 `curl -i -X POST http://localhost:8000/api/v1/incidents/INC-xxxx/execute -H "X-User-Role: viewer"` |
| 预期结果 | 返回 HTTP 403 或被 RBAC 拒绝 |

### TC-EXEC-003 operator 角色可以触发已审批 Incident 执行

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已存在 `approval_status=approved` 的 Incident |
| 操作步骤 | 执行 `curl -i -X POST http://localhost:8000/api/v1/incidents/INC-xxxx/execute -H "X-User-Role: operator"` |
| 预期结果 | 返回 `{"status":"accepted","incident_id":"INC-xxxx"}`，后台产生执行记录 |

### TC-EXEC-004 非白名单命令不允许执行

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 类型 | 安全测试 |
| 验证方式 | 执行单元测试 `.venv/bin/python -m unittest tests.test_executor -v` |
| 预期结果 | 不在白名单内的命令被拒绝，执行器不会运行危险命令 |

### TC-EXEC-005 只读命令不会作为自动恢复动作执行

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 类型 | 安全测试 |
| 验证方式 | 执行 `.venv/bin/python -m unittest tests.test_executor -v` |
| 预期结果 | `kubectl get`、`kubectl describe` 等只读命令会被跳过，不作为变更动作执行 |

## 17. 恢复验证和报告测试用例

### TC-VERIFY-001 执行成功后生成报告

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 前置条件 | 自动执行 E2E 已执行成功 |
| 操作步骤 | 查询 `http://localhost:8000/api/v1/reports/INC-xxxx` |
| 预期结果 | 返回报告 JSON，包含 `content` 和 `fault_patterns` |

### TC-VERIFY-002 Markdown 报告内容完整

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已生成报告 |
| 操作步骤 | 执行 `curl -s "http://localhost:8000/api/v1/reports/INC-xxxx?format=markdown"` |
| 预期结果 | 包含标题、摘要、影响范围、根因、执行结果、验证结果、后续建议 |

### TC-VERIFY-003 验证失败时升级人工

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 类型 | 单元测试优先 |
| 验证方式 | 执行 `.venv/bin/python -m unittest tests.test_verify -v` |
| 预期结果 | 验证失败时 `approval_status` 被更新为 `escalated` |

## 18. Web Console 测试用例

### TC-WEB-001 首页访问

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 操作步骤 | 打开 `http://localhost:8000` |
| 预期结果 | 可以看到 Incident 列表入口和主要导航 |

### TC-WEB-002 Incident 详情页

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 前置条件 | 已存在 Incident |
| 操作步骤 | 打开 `http://localhost:8000/incident-detail.html?id=INC-xxxx` |
| 预期结果 | 页面展示告警信息、根因、证据、Runbook、风险评估、审批状态、执行记录和报告入口 |

### TC-WEB-003 执行记录页

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 打开 `http://localhost:8000/executions.html` |
| 预期结果 | 可以看到最近执行记录 |

### TC-WEB-004 故障报告页

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 打开 `http://localhost:8000/reports.html` |
| 预期结果 | 可以看到最近故障报告，支持查看 Markdown 内容 |

### TC-WEB-005 前端静态页面回归

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 验证方式 | 执行 `.venv/bin/python -m unittest tests.test_web_console -v` |
| 预期结果 | Web Console 静态页面中关键 API 路径存在 |

## 19. 数据库验证用例

### TC-DB-001 Incident 表写入

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已触发一条告警 |
| 操作步骤 | 执行下面 SQL |
| 预期结果 | 可以看到最新 Incident，包含 `approval_status`、`runbook_name`、`risk_assessment` |

```bash
docker compose exec -T postgres psql -U opsagent -d ops_agent \
  -c "select id, service, alert_name, status, approval_status, runbook_name, created_at from incidents order by created_at desc limit 5;"
```

### TC-DB-002 Execution 表写入

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已执行 Runbook 自动执行或 AI 自动执行 |
| 操作步骤 | 执行下面 SQL |
| 预期结果 | 可以看到执行动作、操作者、状态、结果和 AI 重试轮次 |

```bash
docker compose exec -T postgres psql -U opsagent -d ops_agent \
  -c "select incident_id, action, operator, status, created_at, completed_at from executions order by created_at desc limit 5;"
```

### TC-DB-003 Report 表写入

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已生成报告 |
| 操作步骤 | 执行下面 SQL |
| 预期结果 | 可以看到报告和故障特征沉淀 |

```bash
docker compose exec -T postgres psql -U opsagent -d ops_agent \
  -c "select incident_id, jsonb_pretty(fault_patterns), created_at from reports order by created_at desc limit 5;"
```

### TC-DB-004 审计日志写入

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已执行审批或自动执行 |
| 操作步骤 | 执行下面 SQL |
| 预期结果 | 可以看到审批、AI 方案生成、执行、验证、重试、自省、报告等审计事件 |

```bash
docker compose exec -T postgres psql -U opsagent -d ops_agent \
  -c "select incident_id, operator, action, detail, created_at from audit_logs order by created_at desc limit 10;"
```

## 20. Demo 服务和故障注入测试用例

### TC-DEMO-001 Demo 服务健康检查

| 字段 | 内容 |
|------|------|
| 优先级 | P0 |
| 操作步骤 | 执行 `curl -s http://localhost:8081/actuator/health` |
| 预期结果 | 返回 Spring Boot 健康状态 |

### TC-DEMO-002 CPU 故障开关

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | order-service 端口转发正常 |
| 操作步骤 | 执行 `curl -s -X POST "http://localhost:8081/fault/cpu?enable=true"` |
| 预期结果 | CPU 故障开启，稍后 Prometheus 可能出现 HighCPUUsage 告警 |
| 清理方式 | 执行 `curl -s -X POST http://localhost:8081/fault/reset` |

### TC-DEMO-003 Demo 服务重新部署

| 字段 | 内容 |
|------|------|
| 优先级 | P2 |
| 操作步骤 | 执行 `./ops.sh demo restart` |
| 预期结果 | Java 服务重新构建、镜像重新加载到 Kind、Deployment rollout 成功 |

## 21. 异常和边界测试用例

### TC-NEG-001 查询不存在的 Incident

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 执行 `curl -i http://localhost:8000/api/v1/incidents/INC-NOT-FOUND` |
| 预期结果 | 返回 HTTP 404 |

### TC-NEG-002 查询不存在的报告

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 执行 `curl -i http://localhost:8000/api/v1/reports/INC-NOT-FOUND` |
| 预期结果 | 返回 HTTP 404 |

### TC-NEG-003 飞书回调缺少 action

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 向 `/api/v1/approvals/callback` 发送缺少 `action.value.action` 的请求 |
| 预期结果 | 返回错误响应或日志记录无效回调，不应更新 Incident |

### TC-NEG-004 飞书回调缺少 incident_id

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 操作步骤 | 向 `/api/v1/approvals/callback` 发送缺少 `incident_id` 的请求 |
| 预期结果 | 返回错误响应或日志记录无效回调，不应触发执行 |

### TC-NEG-005 Agent 重启后继续读取历史数据

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 前置条件 | 已存在 Incident、Execution、Report |
| 操作步骤 | 执行 `./ops.sh restart` 后查询 `/api/v1/incidents`、`/api/v1/executions`、`/api/v1/reports` |
| 预期结果 | 历史数据仍可查询 |

## 22. 清理和恢复用例

### TC-CLEAN-001 恢复 order-service 默认副本

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 场景 | 执行过自动执行 E2E 后 |
| 操作步骤 | 执行 `kubectl scale deployment order-service -n demo --replicas=2 --context kind-ops-agent` |
| 预期结果 | `order-service` 恢复为 2 个副本 |

### TC-CLEAN-002 重置 Demo 故障

| 字段 | 内容 |
|------|------|
| 优先级 | P1 |
| 场景 | 执行过 CPU 故障注入 |
| 操作步骤 | 执行 `curl -s -X POST http://localhost:8081/fault/reset` |
| 预期结果 | Demo 服务故障状态清空 |

### TC-CLEAN-003 停止本地环境但保留数据

| 字段 | 内容 |
|------|------|
| 优先级 | P2 |
| 操作步骤 | 执行 `./ops.sh stop` |
| 预期结果 | 托管进程和 Docker Compose 停止，Kind 集群和数据卷保留 |

### TC-CLEAN-004 删除本地环境

| 字段 | 内容 |
|------|------|
| 优先级 | P2 |
| 操作步骤 | 执行 `./ops.sh clean` |
| 预期结果 | Kind 集群删除，PostgreSQL 和 Redis 数据卷默认保留 |

### TC-CLEAN-005 删除全部本地数据

| 字段 | 内容 |
|------|------|
| 优先级 | P2 |
| 操作步骤 | 执行 `./ops.sh clean --all` |
| 预期结果 | Kind 集群、托管进程、PostgreSQL 和 Redis 数据卷都被删除 |
| 风险提示 | 会清空本地测试数据，执行前确认不需要保留历史 Incident |

## 23. 验收通过标准

一次完整验收建议满足以下条件：

- `./ops.sh status` 显示 Agent、Prometheus、Alertmanager、Loki、Grafana、order-service 正常。
- `.venv/bin/python -m unittest discover -s tests -p 'test*.py' -v` 全部通过。
- `./ops.sh test ai` 通过。
- `tests/e2e_phase2.sh` 通过。
- `tests/e2e_phase3.sh` 通过。
- `tests/e2e_retry_loop.sh` 通过。
- `tests/e2e_full_pipeline.sh` 通过。
- Web Console 首页、详情页、执行记录页、报告页可以访问，详情页能展示重试时间线和审计日志。
- Grafana 可以登录，Demo 服务看板有数据。
- 相同 `fingerprint` 在 300 秒内重复发送时不会创建第二个 Incident。
- 如果配置了飞书，真实点击「批准执行」「AI 自动执行」「继续 AI 执行」后，数据库状态更新，原卡片按钮被结果卡片替换。
- 数据库中可以查询到 Incident、Execution、Report、AuditLog，以及 AI 兜底相关的 `retry_count` / `retry_history`。

## 24. 常见失败排查

### 24.1 Agent 不可达

检查：

```bash
./ops.sh status
./ops.sh logs agent
curl -i http://localhost:8000/health
```

常见原因：

- 8000 端口被其他进程占用。
- `.venv` 未创建或依赖未安装。
- PostgreSQL 或 Redis 未启动。
- 修改代码后没有执行 `./ops.sh restart`。

### 24.2 Prometheus 没有 Demo 指标

检查：

```bash
kubectl get deployments -n demo --context kind-ops-agent
curl -s "http://localhost:9090/api/v1/query?query=up%7Bnamespace%3D%22demo%22%7D"
```

常见原因：

- Demo 服务未部署。
- Prometheus 端口转发未启动。
- ServiceMonitor 还未被 Prometheus 发现，需要等待一会儿。

### 24.3 Grafana 看不到 Demo 服务

检查：

- 是否打开了 `Demo Services Overview` 看板，而不是只看 Kubernetes mixin。
- Prometheus targets 是否有 demo namespace。
- `./ops.sh restart` 后再刷新 Grafana。

### 24.4 飞书按钮点击后卡片没有变化

检查：

```bash
./ops.sh logs agent
```

重点看日志：

- `收到飞书审批回调`
- `审批状态已更新`
- `更新飞书卡片完成`

说明：

- 如果是 curl 模拟回调，通常不会携带真实 message id，因此只能验证数据库更新和后台执行。
- 如果是真实飞书卡片，必须保证 `.env` 中飞书应用凭证正确，回调请求能携带消息上下文，并且应用有更新卡片权限。

### 24.5 自动执行后副本数变为 4

这是当前 CPU Runbook 的预期行为。恢复命令：

```bash
kubectl scale deployment order-service -n demo --replicas=2 --context kind-ops-agent
```

### 24.6 重复告警仍然重复发卡片

检查：

```bash
./ops.sh logs agent
```

预期：

- 第二次同 fingerprint 告警应该出现 `重复告警已跳过`。
- 工作流应在 parse 后结束，不再进入上下文采集、诊断、Runbook、风险和飞书通知。

如果仍重复发卡片，优先确认：

- 是否发送了完全相同的 `fingerprint`。
- 是否超过 `ALERT_DEDUP_WINDOW`。
- 是否重启或清空过 Redis。
- 当前运行的 Agent 是否已经加载最新代码，必要时执行 `./ops.sh restart`。
