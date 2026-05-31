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

### 1. 环境准备

```bash
# 安装依赖工具
brew install docker kind helm kubectl

# 启动本地开发环境（PostgreSQL + Redis）
cp .env.example .env   # 编辑 .env 填入 DeepSeek API Key 和飞书配置
docker compose up -d
```

### 2. 创建 K8S 集群

```bash
kind create cluster --name ops-agent --config kind-config.yaml
```

### 3. 部署可观测栈

```bash
# Prometheus + Alertmanager + Grafana
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus prometheus-community/kube-prometheus-stack \
  -f k8s/monitoring/prometheus-values.yaml \
  --namespace monitoring --create-namespace

# Loki + Promtail
helm repo add grafana https://grafana.github.io/helm-charts
helm install loki grafana/loki \
  -f k8s/monitoring/loki-values.yaml \
  --namespace monitoring
helm install promtail grafana/promtail \
  -f k8s/monitoring/promtail-values.yaml \
  --namespace monitoring
```

### 4. 构建并部署样例服务

```bash
cd demo-services
mvn clean package -DskipTests

# 构建镜像并加载到 kind
for svc in frontend order payment inventory; do
  docker build -t demo-${svc}:latest -f ${svc}-service/Dockerfile .
  kind load docker-image demo-${svc}:latest --name ops-agent
done

# 部署到 K8S
kubectl apply -f k8s/demo-services/
```

### 5. 启动 Agent

```bash
cd agent
pip install -r requirements.txt
uvicorn agent.main:app --host 0.0.0.0 --port 8000
```

### 6. 端到端测试

```bash
# 触发 CPU 故障 → 等待告警 → 验证诊断
bash tests/e2e_phase1.sh
```

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
