# Ops AI Agent

智能运维 Agent 系统——自动接收告警、诊断根因、生成处置方案并推送飞书通知。

**Phase 1（只读诊断）**：告警 → 自动查询可观测数据 → 生成诊断结论 → 飞书卡片推送，不执行任何变更。

## 架构

```
Alertmanager → FastAPI Agent → LangGraph Workflow (Alert → Context → RCA)
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
              Prometheus         Loki          Kubernetes
                    │               │               │
                    └───────────────┴───────────────┘
                                    │
                                    ▼
                              飞书卡片通知
```

## 技术栈

| 层次 | 组件 |
|------|------|
| Agent 框架 | Python 3.11+ / FastAPI / LangGraph |
| LLM | DeepSeek-V4-Flash (OpenAI Compatible API) |
| 数据层 | PostgreSQL 16 + Redis 7 |
| 可观测 | Prometheus + Grafana + Alertmanager + Loki + Promtail |
| 样例业务 | Java 17 + Spring Boot 3.3 + Micrometer |
| 消息通道 | 飞书 Open API（Bot 消息 + 交互卡片） |
| 基础设施 | Docker + kind (Kubernetes) + Helm |

## 快速开始

### 1. 安装系统依赖

```bash
# macOS
brew install docker kind helm kubectl maven python

# Linux 请使用发行版包管理器安装：
# Docker + Compose v2、kubectl、kind、Helm、Maven、Python 3 和 curl
```

启动 Docker Desktop 或本机 Docker 服务。

### 2. 首次一键启动

```bash
cp .env.example .env
# 可选：编辑 .env，填入 DeepSeek API Key 和飞书应用凭证

./ops.sh bootstrap
```

`bootstrap` 会自动准备 Python 虚拟环境、本地 PostgreSQL 和 Redis、Kind
集群、Prometheus、Alertmanager、Grafana、Loki、Promtail、四个样例服务和
Agent。首次构建需要下载镜像与 Maven 依赖，会比日常启动更久。

### 3. 日常使用

```bash
./ops.sh start          # 启动已有环境
./ops.sh restart        # 重启 Agent、proxy 和 port-forward
./ops.sh status         # 查看组件健康状态
./ops.sh logs agent     # 查看 Agent 日志
./ops.sh demo restart   # 重新构建并部署样例服务
./ops.sh test           # 执行真实 CPU 故障注入 E2E
./ops.sh stop           # 停止后台进程和 Docker Compose
./ops.sh help           # 查看完整命令列表
```

常用地址：

| 服务 | 地址 |
|------|------|
| Agent API | `http://localhost:8000` |
| Agent API 文档 | `http://localhost:8000/docs` |
| Prometheus | `http://localhost:9090` |
| Alertmanager | `http://localhost:9093` |
| Loki | `http://localhost:3100` |
| Grafana | `http://localhost:30030`，账号 `admin/admin123` |

完整的手工部署步骤和排障命令见
[`docs/deployment.md`](docs/deployment.md)。


## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | Agent 健康检查 |
| `/api/v1/alerts` | POST | Alertmanager Webhook 回调 |
| `/api/v1/incidents` | GET | 查询 Incident 列表（支持 `?status=` 过滤） |
| `/api/v1/incidents/{id}` | GET | 查询 Incident 详情（含诊断结论和证据链） |

## 项目结构

```
ops-ai-agent/
├── agent/                  # Python Agent 服务
│   ├── agents/             # Alert / RCA / Supervisor Agent
│   ├── api/v1/             # Webhook + Incident API
│   ├── channels/           # 飞书 SDK 封装
│   ├── db/                 # SQLAlchemy ORM + CRUD
│   ├── llm/                # DeepSeek LLM 适配层
│   ├── templates/cards/    # 飞书卡片模板
│   ├── tools/              # Prometheus / Loki / K8S / CMDB 查询工具
│   └── workflows/          # LangGraph 状态图定义
├── demo-services/          # Java Spring Boot 样例微服务
│   ├── frontend-service/   # :8080
│   ├── order-service/      # :8081
│   ├── payment-service/    # :8082
│   └── inventory-service/  # :8083
├── k8s/
│   ├── demo-services/      # 样例服务 K8S 部署清单
│   └── monitoring/         # 可观测栈 Helm values
├── ops.sh                  # 本地环境一键管理入口
├── docker-compose.yml      # 本地开发环境
├── kind-config.yaml        # kind 集群配置
└── tests/                  # E2E 测试脚本
```

## 开发路线

| Phase | 周期 | 核心交付 |
|-------|------|----------|
| Phase 1 | 2 周 | 只读诊断：告警→诊断→飞书通知 |
| Phase 2 | 2 周 | 方案生成 + 人工审批 |
| Phase 3 | 2 周 | 自动执行 + 验证 + 沉淀 |
