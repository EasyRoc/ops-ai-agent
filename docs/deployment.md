# Phase 1 部署操作手册

> 适用版本：Phase 1 只读诊断 | 最后更新：2026-05-31

---

## 目录

1. [环境要求](#1-环境要求)
2. [组件一览](#2-组件一览)
3. [前置依赖安装](#3-前置依赖安装)
4. [克隆项目与配置](#4-克隆项目与配置)
5. [启动本地数据服务](#5-启动本地数据服务)
6. [创建 K8S 集群并部署可观测栈](#6-创建-k8s-集群并部署可观测栈)
7. [构建并部署样例业务服务](#7-构建并部署样例业务服务)
8. [启动 Agent 服务](#8-启动-agent-服务)
9. [飞书 Bot 配置](#9-飞书-bot-配置)
10. [验证各组件](#10-验证各组件)
11. [端到端联调](#11-端到端联调)
12. [常见问题](#12-常见问题)

---

## 1. 环境要求

### 硬件最低配置

| 资源 | 要求 |
|------|------|
| CPU | 4 核以上 |
| 内存 | 16 GB 以上 |
| 磁盘 | 50 GB 可用空间 |

### 操作系统

- macOS 13+（本文档以 macOS 为准）
- Linux（命令基本兼容，包管理器改为对应工具）

---

## 2. 组件一览

整个 Phase 1 包含以下组件：

```
┌─────────────────────────────────────────────────┐
│                   你的 Mac                       │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │PostgreSQL│  │  Redis   │  │  FastAPI Agent │  │
│  │  :5432   │  │  :6379   │  │  :8000         │  │
│  └──────────┘  └──────────┘  └───────────────┘  │
│       Docker Compose              本地 Python    │
│                                                  │
│  ┌─────────────────────────────────────────────┐ │
│  │              kind 集群 (K8S)                 │ │
│  │                                              │ │
│  │  ┌──────────┐ ┌───────────┐ ┌────────────┐  │ │
│  │  │Prometheus│ │Alertmanager│ │  Grafana    │  │ │
│  │  │  :30090  │ │           │ │  :30030     │  │ │
│  │  └──────────┘ └───────────┘ └────────────┘  │ │
│  │  ┌──────────┐ ┌───────────┐                 │ │
│  │  │  Loki    │ │ Promtail   │                 │ │
│  │  └──────────┘ └───────────┘                 │ │
│  │                                              │ │
│  │  ┌──────────────────────────────────────┐   │ │
│  │  │  4 个 Spring Boot 样例服务 (demo ns)  │   │ │
│  │  │  frontend :8080  order :8081          │   │ │
│  │  │  payment :8082   inventory :8083       │   │ │
│  │  └──────────────────────────────────────┘   │ │
│  └─────────────────────────────────────────────┘ │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │         DeepSeek API (云端)               │   │
│  │       https://api.deepseek.com/v1         │   │
│  └──────────────────────────────────────────┘   │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │        飞书 Open API (云端)               │   │
│  │     https://open.feishu.cn/open-apis      │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

### 端口规划

| 端口 | 组件 | 说明 |
|------|------|------|
| 5432 | PostgreSQL | 数据库 |
| 6379 | Redis | 告警去重缓存 |
| 8000 | FastAPI Agent | Agent 主服务 |
| 8001 | kubectl proxy | K8S API 代理（Agent 查询 K8S 用） |
| 9090 | Prometheus | Prometheus UI（port-forward） |
| 30090 | Prometheus | Prometheus NodePort |
| 30030 | Grafana | Grafana NodePort |
| 8080-8083 | 样例服务 | frontend/order/payment/inventory |
| 30080/30443 | kind 集群 | Ingress NodePort 预留 |

---

## 3. 前置依赖安装

### 3.1 macOS 一键安装

```bash
# 安装 Homebrew（如未安装）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 安装所有依赖
brew install docker kind helm kubectl openjdk@17 maven
```

安装后验证版本：

```bash
docker --version        # ≥ 26.0
kind version            # ≥ 0.22
helm version            # ≥ 3.14
kubectl version --client # ≥ 1.29
java --version          # ≥ 17
mvn --version           # ≥ 3.9
```

### 3.2 启动 Docker Desktop

确保 Docker Desktop 已启动并运行。验证：

```bash
docker ps
# 应正常输出（可能为空列表），不报错即可
```

> **注意：** 以下所有操作都需要 Docker Desktop 在后台运行。

---

## 4. 克隆项目与配置

### 4.1 克隆仓库

```bash
git clone git@github.com:EasyRoc/ops-ai-agent.git
cd ops-ai-agent
```

### 4.2 配置环境变量

```bash
cp .env.example .env   # 如没有 .env.example，直接编辑 .env
```

编辑 `.env`，填写 DeepSeek API Key；如需飞书卡片通知，再填写飞书应用凭证：

```ini
# DeepSeek API（必填）
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxx

# 飞书应用（可选，不用飞书可留空）
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

```

其他配置项使用默认值即可：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| POSTGRES_USER | opsagent | 数据库用户 |
| POSTGRES_PASSWORD | opsagent123 | 数据库密码 |
| POSTGRES_DB | ops_agent | 数据库名 |
| POSTGRES_PORT | 5432 | 数据库端口 |
| REDIS_PORT | 6379 | Redis 端口 |
| AGENT_PORT | 8000 | Agent 端口 |
| DEEPSEEK_MODEL | deepseek-v4-flash | LLM 模型 |
| PROMETHEUS_URL | http://localhost:9090 | Prometheus 地址 |
| LOKI_URL | http://localhost:3100 | Loki 地址 |

### 4.3 创建 Python 虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 5. 启动本地数据服务

PostgreSQL 和 Redis 通过 Docker Compose 运行。

```bash
# 在项目根目录
docker compose up -d
```

验证：

```bash
docker compose ps
# 预期输出:
# NAME                    STATUS
# ops-ai-agent-postgres-1  Up (healthy)
# ops-ai-agent-redis-1     Up (healthy)

# 验证 PostgreSQL
docker compose exec postgres psql -U opsagent -d ops_agent -c "\dt"
# 应列出 incidents, executions, reports, audit_logs 四张表

# 验证 Redis
docker compose exec redis redis-cli ping
# 输出: PONG
```

> PostgreSQL 使用 `agent/db/migrations/001_init.sql` 自动初始化表结构。

---

## 6. 创建 K8S 集群并部署可观测栈

### 6.1 创建 kind 集群

```bash
kind create cluster --name ops-agent --config kind-config.yaml
```

验证：

```bash
kubectl cluster-info --context kind-ops-agent
kubectl get nodes --context kind-ops-agent
# 预期: 3 个节点 (1 control-plane + 2 worker)，状态 Ready
```

> 之后所有 kubectl 命令默认使用 `kind-ops-agent` 上下文。如果同时操作多个集群，在每个 kubectl 命令后加 `--context kind-ops-agent`。

### 6.2 部署 Prometheus + Alertmanager + Grafana

```bash
# 添加 Helm 仓库
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# 安装 kube-prometheus-stack
helm install prometheus prometheus-community/kube-prometheus-stack \
  -f k8s/monitoring/prometheus-values.yaml \
  -f k8s/monitoring/grafana-values.yaml \
  --namespace monitoring --create-namespace
```

等待所有 Pod 就绪（约 1-2 分钟）：

```bash
kubectl get pods -n monitoring --context kind-ops-agent -w
# 等待所有 Pod STATUS 变为 Running，READY 为 1/1 或 2/2
# 按 Ctrl+C 退出 watch
```

确认关键组件运行正常：

```bash
kubectl get pods -n monitoring --context kind-ops-agent | grep -E "prometheus|alertmanager|grafana"
```

### 6.3 部署 Loki + Promtail

```bash
# 部署 Loki（kubectl 直接部署，无需 Helm）
kubectl apply -f k8s/monitoring/loki.yaml --context kind-ops-agent

# 安装 Promtail（Helm）
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
helm install promtail grafana/promtail \
  -f k8s/monitoring/promtail-values.yaml \
  --namespace monitoring
```

验证：

```bash
kubectl get pods -n monitoring --context kind-ops-agent | grep -E "loki|promtail"
# 预期: loki-xxx Running (1个), promtail-* Running (每个节点一个)
```

### 6.4 验证可观测栈

```bash
# Prometheus
kubectl port-forward -n monitoring svc/prometheus-operated 9090:9090 --context kind-ops-agent &
curl -s http://localhost:9090/-/healthy
# 输出: Prometheus Server is Healthy.

# Alertmanager
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093 --context kind-ops-agent &
curl -s http://localhost:9093/-/healthy
# 输出: OK

# Loki
kubectl port-forward -n monitoring svc/loki 3100:3100 --context kind-ops-agent &
curl -s http://localhost:3100/ready
# 输出: ready

# Grafana
kubectl port-forward -n monitoring svc/prometheus-grafana 30030:80 --context kind-ops-agent &
curl -s -o /dev/null -w "%{http_code}" http://localhost:30030/login
# 输出: 200
```

Grafana 登录信息：
- URL: `http://localhost:30030`
- 用户名: `admin`
- 密码: `admin123`

---

## 7. 构建并部署样例业务服务

### 7.1 构建 Java 项目

```bash
cd demo-services

# 编译打包（跳过测试以加快速度）
mvn clean package -DskipTests
# 预期: BUILD SUCCESS
```

### 7.2 构建 Docker 镜像

为每个服务单独编写 Dockerfile（此处以 frontend 为例，其余三个同理）：

```dockerfile
# demo-services/frontend-service/Dockerfile
FROM eclipse-temurin:17-jdk AS builder
WORKDIR /app
COPY ../pom.xml .
COPY ../common/ ./common/
COPY ./ ./
RUN apt-get update && apt-get install -y maven && mvn clean package -DskipTests -pl . -am

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=builder /app/target/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
```

> 实际构建时可以用宿主机的 Maven 先打包，然后用更简单的 Dockerfile 直接复制 JAR 包。这里推荐使用宿主机构建方式：

```bash
# 方案 A：宿主机构建 JAR + 简单 Dockerfile（推荐）
cd demo-services

# 仓库中已提供每个服务的简单 Dockerfile
for svc in frontend order payment inventory; do
  # 构建服务 JAR
  mvn package -DskipTests -pl ${svc}-service -am

  # 构建 Docker 镜像
  docker build -t demo-${svc}:latest -f ${svc}-service/Dockerfile ${svc}-service/
done
```

### 7.3 加载镜像到 kind 集群

```bash
for svc in frontend order payment inventory; do
  kind load docker-image demo-${svc}:latest --name ops-agent
done
```

验证镜像已加载：

```bash
docker exec -it ops-agent-control-plane crictl images | grep demo-
# 应显示 4 个 demo-* 镜像
```

### 7.4 部署到 K8S

```bash
cd /path/to/ops-ai-agent  # 回到项目根目录

kubectl apply -f k8s/demo-services/namespace.yaml --context kind-ops-agent
kubectl wait --for=jsonpath='{.status.phase}'=Active namespace/demo --timeout=60s --context kind-ops-agent
kubectl apply -f k8s/demo-services/ --context kind-ops-agent
```

等待所有 Pod 就绪：

```bash
kubectl get pods -n demo --context kind-ops-agent -w
# 预期: 8 个 Pod (每个服务 2 副本) 全部 Running
# 按 Ctrl+C 退出 watch
```

### 7.5 验证样例服务

```bash
# 验证 Prometheus 已发现服务
kubectl get servicemonitors -n demo --context kind-ops-agent
# 预期: frontend-service, order-service, payment-service, inventory-service

# Port-forward 一个服务验证
kubectl port-forward -n demo svc/order-service 8081:8081 --context kind-ops-agent &
curl -s http://localhost:8081/actuator/health
# 输出: {"status":"UP"}

# 触发故障注入接口
curl -X POST "http://localhost:8081/fault/cpu?enable=true"
# 输出: CPU burn: true

# 重置故障
curl -X POST http://localhost:8081/fault/reset
# 输出: All faults reset
```

故障注入接口一览：

| 接口 | 方法 | 参数 | 说明 |
|------|------|------|------|
| `/fault/cpu` | POST | `enable=true/false` | 触发 CPU 100% 死循环 |
| `/fault/memory` | POST | `mb=10` | 分配指定 MB 堆内存 |
| `/fault/error` | POST | `enable=true/false` | 所有请求返回 500 |
| `/fault/latency` | POST | `ms=5000` | 所有请求延迟 N ms |
| `/fault/reset` | POST | - | 重置所有故障 |
| `/fault/status` | GET | - | 查看当前故障状态 |

---

## 8. 启动 Agent 服务

### 8.1 启动 kubectl proxy

Agent 通过 kubectl proxy 访问 K8S API，需在后台运行：

```bash
kubectl proxy --port=8001 --context kind-ops-agent &
```

### 8.2 启动可观测栈 Port-Forward

```bash
kubectl port-forward -n monitoring svc/prometheus-operated 9090:9090 --context kind-ops-agent &
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093 --context kind-ops-agent &
kubectl port-forward -n monitoring svc/loki 3100:3100 --context kind-ops-agent &
kubectl port-forward -n monitoring svc/prometheus-grafana 30030:80 --context kind-ops-agent &
```

### 8.3 启动 Agent

```bash
source .venv/bin/activate   # 在项目根目录激活虚拟环境

# 开发模式启动（支持热重载）
uvicorn agent.main:app --host 0.0.0.0 --port 8000 --reload
```

验证：

```bash
# 健康检查
curl -s http://localhost:8000/health
# 输出: {"status":"ok"}

# 查看 API 文档
open http://localhost:8000/docs
```

### 8.4 验证 Agent 数据库连接

```bash
curl -s http://localhost:8000/api/v1/incidents
# 输出: {"total": 0, "incidents": []}
```

---

## 9. 飞书 Bot 配置

> 此步骤为可选。不配置飞书时，Agent 仍可接收告警、执行诊断、存储结果，只是不会推送卡片通知。

### 9.1 创建飞书应用

1. 访问 [飞书开放平台](https://open.feishu.cn/)
2. 创建企业自建应用
3. 在 **权限管理** 中添加以下权限：
   - `im:message` — 获取消息
   - `im:message:send_as_bot` — 以 Bot 身份发送消息
   - `im:chat` — 获取群信息
4. 发布应用（需管理员审批）

### 9.2 获取凭证

在应用 **凭证与基础信息** 页面复制：

```ini
# .env
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 9.3 添加 Bot 到群聊

1. 在飞书中创建一个运维告警群
2. 在群设置 → 群机器人 → 添加机器人 → 选择你创建的应用
3. 通过飞书 **事件订阅** 获取 `chat_id`
4. 将 `agent/tools/cmdb.py` 中 `oc_chat_*` 示例值替换为对应群聊的真实 `chat_id`

> CMDB Mock 中的 `oc_chat_*` 仅为占位符。未替换时不影响告警接收和自动诊断，但飞书卡片无法投递。

---

## 10. 验证各组件

### 验证清单

| # | 检查项 | 命令 | 预期结果 |
|---|--------|------|----------|
| 1 | PostgreSQL | `docker compose exec postgres pg_isready` | accepting connections |
| 2 | Redis | `docker compose exec redis redis-cli ping` | PONG |
| 3 | K8S 集群 | `kubectl get nodes --context kind-ops-agent` | 3 Ready |
| 4 | Prometheus | `curl -s http://localhost:9090/-/healthy` | Prometheus Server is Healthy. |
| 5 | Loki | `curl -s http://localhost:3100/ready` | ready |
| 6 | Grafana | `curl -s -o /dev/null -w "%{http_code}" http://localhost:30030/login` | 200 |
| 7 | 样例服务 | `kubectl get pods -n demo --context kind-ops-agent` | 8 Running |
| 8 | Agent | `curl -s http://localhost:8000/health` | {"status":"ok"} |
| 9 | Prom 指标 | `curl -s "http://localhost:9090/api/v1/label/service/values"` | 含 demo 服务名 |
| 10 | Agent DB | `curl -s http://localhost:8000/api/v1/incidents` | {"total":0,"incidents":[]} |

---

## 11. 端到端联调

### 11.1 模拟完整告警流程

直接向 Agent 发送模拟告警（无需等待 Prometheus 真实触发）：

```bash
# 模拟 Alertmanager Webhook
curl -X POST http://localhost:8000/api/v1/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "receiver": "ops-agent-webhook",
    "alerts": [
      {
        "fingerprint": "test-cpu-001",
        "labels": {
          "alertname": "HighCPUUsage",
          "service": "order-service",
          "severity": "P2"
        },
        "annotations": {
          "summary": "order-service CPU > 90%",
          "value": "95.5"
        },
        "startsAt": "2026-05-31T10:00:00Z"
      }
    ]
  }'
```

查看诊断结果：

```bash
# 获取 Incident 列表
curl -s http://localhost:8000/api/v1/incidents | python3 -m json.tool

# 查看具体 Incident 详情（替换 INC-XXXX 为实际 ID）
curl -s http://localhost:8000/api/v1/incidents/INC-XXXXXXXXXXXX | python3 -m json.tool
```

预期诊断结果应包含：
- `root_cause`: 根因判断（如 "单实例异常或代码死循环"）
- `confidence`: 置信度（0-1）
- `evidence`: 证据数组
- `status`: `diagnosed`

### 11.2 通过真实故障触发（完整链路）

```bash
# 1. 转发 order-service 端口并触发 CPU 故障
kubectl port-forward -n demo svc/order-service 8081:8081 --context kind-ops-agent &
curl -s -X POST "http://localhost:8081/fault/cpu?enable=true"

# 2. 等待 150 秒让 Prometheus 触发告警并转发到 Alertmanager Webhook
#    实际耗时受抓取、规则评估和 Alertmanager 聚合周期影响
sleep 150

# 3. 检查 Prometheus 告警
curl -s "http://localhost:9090/api/v1/alerts" | python3 -c "
import sys,json
alerts = json.load(sys.stdin)['data']['alerts']
for a in alerts:
    print(f\"{a['labels']['alertname']} - {a['labels']['service']} - {a['state']}\")
"

# 4. 检查 Agent 是否创建了 Incident
curl -s http://localhost:8000/api/v1/incidents | python3 -c "
import sys,json
d = json.load(sys.stdin)
print(f\"Total incidents: {d['total']}\")
for i in d['incidents']:
    print(f\"  {i['id']}: {i['service']} - {i['status']} - {i.get('root_cause', 'pending')}\")
"

# 5. 重置故障
curl -s -X POST http://localhost:8081/fault/reset
```

---

## 12. 常见问题

### Q1: docker compose 启动后 PostgreSQL 一直不 healthy？

```bash
# 查看日志
docker compose logs postgres

# 常见原因：端口冲突。检查 5432 是否被占用
lsof -i :5432

# 解决方法：修改 .env 中 POSTGRES_PORT 为其他端口
```

### Q2: kind create cluster 失败？

```bash
# 确保 Docker Desktop 已启动
docker ps

# 如果报 cgroup 相关错误，尝试：
kind create cluster --name ops-agent --config kind-config.yaml --retain
# 查看详细错误
kind export logs ./kind-logs/
```

### Q3: Helm 安装 Prometheus 超时？

```bash
# 检查 Helm 仓库连接
helm repo list
helm repo update

# 使用国内镜像（如网络问题）
# 添加阿里云 Helm 仓库
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
```

### Q4: 样例服务 Pod 一直 ImagePullBackOff？

```bash
# 检查镜像是否已加载到 kind
docker exec -it ops-agent-control-plane crictl images | grep demo-

# 如果没有，重新加载
kind load docker-image demo-order:latest --name ops-agent

# 确认 Deployment 中 imagePullPolicy 为 IfNotPresent
kubectl get deploy -n demo order-service -o yaml | grep imagePullPolicy
```

### Q5: Agent 启动报 "Connection refused" 连接 PostgreSQL？

```bash
# 确认 Docker Compose 服务在运行
docker compose ps

# 确认 .env 中 postgres_host=localhost
# 如果 Agent 在 Docker 容器内运行，需改为 postgres
```

### Q6: Prometheus 没有采集到样例服务指标？

```bash
# 检查 ServiceMonitor 是否正确创建
kubectl get servicemonitors -n demo --context kind-ops-agent

# 检查 Prometheus 目标
kubectl port-forward -n monitoring svc/prometheus-operated 9090:9090 --context kind-ops-agent &
# 浏览器打开 http://localhost:9090/targets
# 搜索 "demo" 确认 targets 状态为 UP
```

### Q7: 飞书收不到卡片通知？

```bash
# 1. 确认 FEISHU_APP_ID 和 FEISHU_APP_SECRET 已正确配置
# 2. 确认 Bot 已被添加到目标群聊
# 3. 查看 Agent 日志中的飞书相关信息
# 4. 使用 curl 直接测试飞书 API：
curl -X POST "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal" \
  -H "Content-Type: application/json" \
  -d '{"app_id":"你的APP_ID","app_secret":"你的APP_SECRET"}'
# 应返回 {"code":0, "tenant_access_token":"..."}
```

### Q8: 如何停止所有服务？

```bash
# 停止本项目后台进程和 Docker Compose，保留 Kind 集群和数据库卷
./ops.sh stop

# 删除 Kind 集群，保留数据库卷
./ops.sh clean

# 完全清除，包括数据库卷
./ops.sh clean --all
```

---

## 附录：一键管理脚本

首次克隆项目后，在仓库根目录执行：

```bash
cp .env.example .env
# 可选：编辑 .env，填入 DeepSeek API Key 和飞书应用凭证
./ops.sh bootstrap
```

常用命令：

| 命令 | 说明 |
|------|------|
| `./ops.sh bootstrap` | 首次初始化并启动完整本地环境 |
| `./ops.sh start` | 启动已有环境，不重新构建样例镜像 |
| `./ops.sh restart` | 重启 Agent、proxy 和 port-forward |
| `./ops.sh stop` | 停止后台进程和 Docker Compose，保留数据 |
| `./ops.sh status` | 查看组件健康状态、Pod 和配置提示 |
| `./ops.sh logs agent` | 持续查看 Agent 日志 |
| `./ops.sh demo start` | 构建、加载并部署样例服务 |
| `./ops.sh demo stop` | 删除样例服务 namespace |
| `./ops.sh demo restart` | 重新构建并部署样例服务 |
| `./ops.sh test` | 执行真实 CPU 故障注入 E2E |
| `./ops.sh clean` | 删除 Kind 集群，保留数据库卷 |
| `./ops.sh clean --all` | 删除 Kind 集群和数据库卷 |
| `./ops.sh help` | 查看完整命令说明 |

脚本在仓库根目录生成 `.ops/pids/` 和 `.ops/logs/`，只停止本项目记录的后台
进程，不会使用宽泛的 `pkill` 影响其他本地环境。

---

> **下一步**：Phase 2 将在此环境基础上增加 Runbook 匹配、风险评估和飞书交互审批功能。
