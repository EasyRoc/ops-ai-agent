# Ops AI Agent

Ops AI Agent 是一个面向本地演示和学习的智能运维系统。它接收 Prometheus
Alertmanager 告警，自动采集指标、日志、Kubernetes 状态和服务信息，生成根因
诊断、匹配 Runbook、评估风险，并通过飞书卡片完成人工审批。

当前版本已覆盖 **Phase 2：Runbook 方案 + 人工审批闭环**。Agent 会出方案、评估
风险、记录审批状态，但不会自动执行变更命令。

## 核心能力

- 告警接入：接收 Alertmanager Webhook，创建并去重 Incident。
- 上下文采集：查询 Prometheus、Loki、Kubernetes 和 Mock CMDB。
- 根因分析：优先调用 DeepSeek；不可用时使用规则兜底。
- 方案生成：根据告警匹配 CPU、OOM、错误率、延迟 Runbook。
- 风险评估：结合操作风险、告警级别、生产环境和核心服务加权。
- 人工审批：飞书卡片支持「批准执行」「拒绝」「转人工」。
- 本地演示：一条脚本启动 Kind、可观测栈、Demo 服务和 Agent。
- Web Console：查看 Incident、处置方案、风险和审批状态。

## 系统架构

![Ops AI Agent 系统架构](docs/img/architecture.svg)

## 告警审批流程

![告警诊断与审批流程](docs/img/approval-flow.svg)

## 快速开始

### 1. 准备依赖

macOS 推荐使用 Homebrew：

```bash
brew install docker kind helm kubectl maven python
```

启动前请确认 Docker Desktop 或本机 Docker 服务已经运行。

### 2. 初始化配置

```bash
cp .env.example .env
```

可选配置：

```bash
# LLM 根因分析
DEEPSEEK_API_KEY=sk-xxx

# 飞书卡片通知
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
SERVICE_CHAT_IDS='{"order-service":"oc_xxx","payment-service":"oc_xxx"}'
```

未配置 DeepSeek 时会使用规则兜底；未配置飞书时，告警接收、诊断和数据库保存仍然可用。

### 3. 一键启动

```bash
./ops.sh bootstrap
```

首次启动会创建 Python 虚拟环境、PostgreSQL、Redis、Kind 集群、Prometheus、
Alertmanager、Grafana、Loki、Promtail、四个 Demo 服务和 Agent。首次拉镜像和
构建 Java 服务会比较慢，日常启动可改用：

```bash
./ops.sh start
```

### 4. 检查状态

```bash
./ops.sh status
```

正常时应看到：

- Agent、Prometheus、Alertmanager、Loki、Grafana 均为 `正常`。
- Demo 服务 Deployment 均为 `2/2`。
- Prometheus targets 显示 `8/8 targets UP`。

## 常用入口

| 入口 | 地址 |
|------|------|
| Web Console | [http://localhost:8000](http://localhost:8000) |
| Agent API 文档 | [http://localhost:8000/docs](http://localhost:8000/docs) |
| Incident API | [http://localhost:8000/api/v1/incidents](http://localhost:8000/api/v1/incidents) |
| Grafana | [http://localhost:30030](http://localhost:30030) |
| Demo 业务看板 | [Demo Services Overview](http://localhost:30030/d/demo-services/demo-services-overview) |
| Prometheus Alerts | [http://localhost:9090/alerts](http://localhost:9090/alerts) |
| Alertmanager | [http://localhost:9093](http://localhost:9093) |

Grafana 本地默认账号：

```text
admin / admin123
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `./ops.sh bootstrap` | 首次初始化并启动完整环境 |
| `./ops.sh start` | 启动已有环境 |
| `./ops.sh restart` | 重启 Agent、proxy 和端口转发 |
| `./ops.sh stop` | 停止后台进程和 Docker Compose，保留数据 |
| `./ops.sh status` | 查看健康状态和配置提示 |
| `./ops.sh logs agent` | 查看 Agent 日志 |
| `./ops.sh demo restart` | 重新构建并部署 Demo 服务 |
| `./ops.sh test` | 运行 CPU 故障注入端到端演示 |
| `./ops.sh clean` | 停止服务并删除 Kind 集群 |
| `./ops.sh clean --all` | 同时删除 PostgreSQL 和 Redis 数据卷 |

## 运行演示

```bash
./ops.sh status
./ops.sh test
```

脚本会向 `order-service` 注入 CPU 故障，等待告警进入 Agent，生成 Incident、
根因诊断、Runbook 方案和风险评估。配置飞书后，群里会收到可审批的诊断卡片。

飞书卡片按钮回调需要公网 HTTPS 地址，配置方法见：

[飞书卡片回调配置指南](docs/feishu-card-callback.md)

## 主要 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | `GET` | Agent 健康检查 |
| `/api/v1/alerts` | `POST` | Alertmanager Webhook |
| `/api/v1/incidents` | `GET` | 查询 Incident 列表 |
| `/api/v1/incidents/{id}` | `GET` | 查询 Incident 详情 |
| `/api/v1/incidents/{id}/approval` | `GET` | 查询审批状态 |
| `/api/v1/approvals/callback` | `POST` | 飞书卡片审批回调 |

## 项目结构

```text
ops-ai-agent/
├── agent/                  # Python Agent 服务
│   ├── agents/             # 告警解析、RCA、Runbook、风险评估
│   ├── api/v1/             # Webhook、Incident、审批 API
│   ├── channels/           # 飞书 Open API 封装
│   ├── db/                 # ORM 和迁移脚本
│   ├── templates/cards/    # 飞书卡片模板
│   ├── tools/              # Prometheus、Loki、Kubernetes、CMDB 工具
│   └── workflows/          # LangGraph 工作流
├── demo-services/          # Java Spring Boot 样例服务
├── k8s/                    # Demo 服务和可观测栈配置
├── runbooks/               # Phase 2 Runbook 模板
├── tests/                  # 单元测试和端到端测试
├── web/                    # 简易 Web Console
├── ops.sh                  # 本地环境管理入口
└── docker-compose.yml      # PostgreSQL 和 Redis
```

## 开发验证

```bash
bash -n ops.sh
bash scripts/tests/test_ops.sh
.venv/bin/python -m unittest discover -s tests -p 'test*.py' -v
```

Phase 2 端到端验收：

```bash
tests/e2e_phase2.sh
```

## 常见问题

**Grafana 看不到 Demo 服务**

先执行 `./ops.sh status`，确认 Prometheus 显示 `8/8 targets UP`。业务指标优先看
[Demo Services Overview](http://localhost:30030/d/demo-services/demo-services-overview)，不要只停留在 Kubernetes 通用看板。

**飞书按钮点击后没有回调**

检查飞书卡片回调地址是否仍然可用，尤其是 cloudflared / ngrok 临时域名是否变化。
详细步骤见 [飞书卡片回调配置指南](docs/feishu-card-callback.md)。

**修改 Demo 服务后如何更新**

```bash
./ops.sh demo restart
```

## 深入文档

- [手工部署与详细排障](docs/deployment.md)
- [飞书卡片回调配置指南](docs/feishu-card-callback.md)
- [技术实现方案](docs/运维%20Agent%20技术实现方案.md)
- [产品需求文档](docs/运维%20Agent%20产品需求文档（PRD）.md)
- [架构分析](docs/关于运维%20Agent%20的架构分析.md)
