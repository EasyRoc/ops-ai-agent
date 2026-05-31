# Phase 1：只读诊断 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建运维 Agent 的只读诊断能力——接收告警、自动查询可观测数据、生成诊断结论并推送到飞书，不执行任何变更操作。

**Architecture:** FastAPI 作为 Agent 服务主框架，LangGraph 编排诊断 Workflow（Alert→Context→RCA），DeepSeek-V4-Flash 作为 LLM 推理引擎，通过飞书 Bot 推送卡片通知。4 个 Java/Spring Boot 样例微服务部署在 kind 集群上，Prometheus + Loki 提供可观测数据源。

**Tech Stack:** Python 3.11+ / FastAPI / LangGraph / DeepSeek-V4-Flash (OpenAI Compatible API) / PostgreSQL / Redis / Java 17 / Spring Boot 3 / Micrometer / Docker / kind / Helm / Prometheus / Grafana / Alertmanager / Loki / 飞书 Open API

---

## File Structure

```
ops-ai-agent/
├── docker-compose.yml              # PostgreSQL + Redis
├── kind-config.yaml                # kind 集群配置
├── .env                            # 环境变量
├── agent/                          # Python Agent 服务
│   ├── main.py                     # FastAPI 入口
│   ├── config.py                   # 配置管理
│   ├── llm/
│   │   └── client.py               # LLM 适配层
│   ├── db/
│   │   ├── models.py               # SQLAlchemy ORM
│   │   ├── crud.py                 # CRUD 操作
│   │   └── migrations/
│   │       └── 001_init.sql        # 建表 DDL
│   ├── api/
│   │   └── v1/
│   │       ├── alerts.py           # Webhook API
│   │       └── incidents.py        # Incident API
│   ├── agents/
│   │   ├── supervisor.py           # Workflow 编排
│   │   ├── alert.py                # 告警处理
│   │   └── rca.py                  # 根因分析
│   ├── workflows/
│   │   └── alert_workflow.py       # LangGraph 状态图
│   ├── tools/
│   │   ├── prometheus.py           # PromQL 封装
│   │   ├── loki.py                 # LogQL 封装
│   │   ├── kubernetes.py           # K8S API 封装
│   │   └── cmdb.py                 # CMDB Mock
│   ├── channels/
│   │   └── feishu.py               # 飞书 SDK 封装
│   └── templates/
│       └── cards/
│           ├── alert_card.json     # 告警通知卡片
│           └── diagnosis_card.json # 诊断结果卡片
├── demo-services/                  # Java 样例业务
│   ├── pom.xml                     # 父 POM
│   ├── common/                     # 公共模块（故障注入）
│   ├── frontend-service/
│   ├── order-service/
│   ├── payment-service/
│   └── inventory-service/
├── k8s/                            # Kubernetes 清单
│   ├── demo-services/              # 样例服务 Deployment/Service
│   └── monitoring/                 # 可观测栈 Helm values
└── runbooks/                       # Runbook 模板
    ├── cpu_high.md
    ├── oom.md
    └── error_rate.md
```

---

### Task 1: Docker 开发环境初始化

**Files:**
- Create: `docker-compose.yml`
- Create: `.env`

- [ ] **Step 1: 编写 .env 配置文件**

```bash
# .env
# PostgreSQL
POSTGRES_USER=opsagent
POSTGRES_PASSWORD=opsagent123
POSTGRES_DB=ops_agent
POSTGRES_PORT=5432

# Redis
REDIS_PORT=6379

# DeepSeek API
DEEPSEEK_API_KEY=sk-your-deepseek-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash

# Agent
AGENT_PORT=8000
AGENT_HOST=0.0.0.0

# Feishu
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_BOT_WEBHOOK=
```

- [ ] **Step 2: 编写 docker-compose.yml**

```yaml
# docker-compose.yml
version: "3.9"

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "${POSTGRES_PORT}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./agent/db/migrations:/docker-entrypoint-initdb.d
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "${REDIS_PORT}:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

- [ ] **Step 3: 启动开发环境并验证**

```bash
docker compose up -d
docker compose ps
# 预期: postgres (healthy), redis (healthy)
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env
git commit -m "feat: add Docker dev environment (PostgreSQL + Redis)"
```

---

### Task 2: kind 集群创建

**Files:**
- Create: `kind-config.yaml`

- [ ] **Step 1: 编写 kind 集群配置**

```yaml
# kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30080
        hostPort: 30080
        protocol: TCP
      - containerPort: 30443
        hostPort: 30443
        protocol: TCP
  - role: worker
  - role: worker
```

- [ ] **Step 2: 创建集群并验证**

```bash
kind create cluster --name ops-agent --config kind-config.yaml
kubectl cluster-info --context kind-ops-agent
kubectl get nodes
# 预期: 3 nodes (1 control-plane, 2 worker), all Ready
```

- [ ] **Step 3: Commit**

```bash
git add kind-config.yaml
git commit -m "feat: add kind cluster config (1 cp + 2 worker)"
```

---

### Task 3: PostgreSQL 表结构

**Files:**
- Create: `agent/db/migrations/001_init.sql`

- [ ] **Step 1: 编写 DDL**

```sql
-- agent/db/migrations/001_init.sql

CREATE TABLE IF NOT EXISTS incidents (
    id              VARCHAR(64) PRIMARY KEY,
    service         VARCHAR(128) NOT NULL,
    env             VARCHAR(32) NOT NULL DEFAULT 'prod',
    severity        VARCHAR(16) NOT NULL,
    status          VARCHAR(32) NOT NULL DEFAULT 'open',
    alert_name      VARCHAR(256),
    alert_value     VARCHAR(128),
    root_cause      TEXT,
    confidence      FLOAT,
    evidence        JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    resolved_at     TIMESTAMP WITH TIME ZONE,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_incidents_service ON incidents(service);
CREATE INDEX idx_incidents_status ON incidents(status);
CREATE INDEX idx_incidents_created ON incidents(created_at DESC);

CREATE TABLE IF NOT EXISTS executions (
    id              SERIAL PRIMARY KEY,
    incident_id     VARCHAR(64) REFERENCES incidents(id),
    action          VARCHAR(128) NOT NULL,
    operator        VARCHAR(64),
    status          VARCHAR(32) NOT NULL DEFAULT 'pending',
    result          JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at    TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_executions_incident ON executions(incident_id);

CREATE TABLE IF NOT EXISTS reports (
    id              SERIAL PRIMARY KEY,
    incident_id     VARCHAR(64) REFERENCES incidents(id),
    content         TEXT NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id              SERIAL PRIMARY KEY,
    incident_id     VARCHAR(64),
    actor           VARCHAR(64) NOT NULL,
    action          VARCHAR(128) NOT NULL,
    detail          JSONB,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_audit_incident ON audit_logs(incident_id);
CREATE INDEX idx_audit_created ON audit_logs(created_at DESC);
```

- [ ] **Step 2: 重启 PostgreSQL 使初始化脚本生效**

```bash
docker compose down postgres && docker compose up -d postgres
docker compose exec postgres psql -U opsagent -d ops_agent -c "\dt"
# 预期: incidents, executions, reports, audit_logs 四张表
```

- [ ] **Step 3: Commit**

```bash
git add agent/db/migrations/001_init.sql
git commit -m "feat: add PostgreSQL schema (incidents, executions, reports, audit_logs)"
```

---

### Task 4: Redis 配置验证

**Files:**
- 无新文件（仅验证 docker-compose 中的 Redis 可用）

- [ ] **Step 1: 验证 Redis 连接**

```bash
docker compose exec redis redis-cli ping
# 预期: PONG
```

- [ ] **Step 2: 无需单独 commit（Redis 配置已在 Task 1 的 docker-compose 中）**

---

### Task 5: FastAPI 项目骨架

**Files:**
- Create: `agent/config.py`
- Create: `agent/main.py`
- Create: `agent/__init__.py`

- [ ] **Step 1: 编写 config.py**

```python
# agent/config.py
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Agent
    agent_host: str = "0.0.0.0"
    agent_port: int = 8000

    # PostgreSQL
    postgres_user: str = "opsagent"
    postgres_password: str = "opsagent123"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "ops_agent"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # DeepSeek API
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-v4-flash"

    # Prometheus
    prometheus_url: str = "http://localhost:9090"

    # Loki
    loki_url: str = "http://localhost:3100"

    # Feishu
    feishu_app_id: str = ""
    feishu_app_secret: str = ""

    # Alertmanager dedup window (seconds)
    alert_dedup_window: int = 300

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
```

- [ ] **Step 2: 编写 main.py**

```python
# agent/main.py
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ops-agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Ops Agent starting on {settings.agent_host}:{settings.agent_port}")
    yield
    logger.info("Ops Agent shutting down")


app = FastAPI(
    title="Ops AI Agent",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 3: 安装 Python 依赖并启动验证**

```bash
cd agent
pip install fastapi uvicorn pydantic-settings asyncpg sqlalchemy redis httpx langgraph openai

# 验证启动
python -c "from agent.main import app; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add agent/config.py agent/main.py agent/__init__.py
git commit -m "feat: FastAPI project skeleton with config management"
```

---

### Task 6: Prometheus 部署

**Files:**
- Create: `k8s/monitoring/prometheus-values.yaml`

- [ ] **Step 1: 编写 Prometheus Helm values**

```yaml
# k8s/monitoring/prometheus-values.yaml
server:
  service:
    type: NodePort
    nodePort: 30090

alertmanager:
  enabled: true
  config:
    global:
      resolve_timeout: 5m
    route:
      receiver: "ops-agent-webhook"
      group_by: ["alertname", "service"]
      group_wait: 10s
      group_interval: 10s
      repeat_interval: 5m
    receivers:
      - name: "ops-agent-webhook"
        webhook_configs:
          - url: "http://host.docker.internal:8000/api/v1/alerts"
            send_resolved: true

serverFiles:
  alerting_rules.yml:
    groups:
      - name: demo-services
        rules:
          - alert: HighCPUUsage
            expr: rate(process_cpu_usage[1m]) * 100 > 90
            for: 1m
            labels:
              severity: P2
            annotations:
              summary: "{{ $labels.service }} CPU > 90%"
              service: "{{ $labels.service }}"

          - alert: HighErrorRate
            expr: rate(http_server_requests_seconds_count{status=~"5.."}[1m]) / rate(http_server_requests_seconds_count[1m]) > 0.05
            for: 1m
            labels:
              severity: P1
            annotations:
              summary: "{{ $labels.service }} error rate > 5%"
              service: "{{ $labels.service }}"
```

- [ ] **Step 2: 安装 Prometheus**

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install prometheus prometheus-community/kube-prometheus-stack \
  -f k8s/monitoring/prometheus-values.yaml \
  --namespace monitoring --create-namespace
kubectl get pods -n monitoring
# 预期: prometheus-*, alertmanager-* 等 Pod 全部 Running
```

- [ ] **Step 3: 验证 Prometheus 可访问**

```bash
kubectl port-forward -n monitoring svc/prometheus-operated 9090:9090 &
curl -s http://localhost:9090/-/healthy
# 预期: Prometheus is Healthy
```

- [ ] **Step 4: Commit**

```bash
git add k8s/monitoring/prometheus-values.yaml
git commit -m "feat: add Prometheus + Alertmanager Helm config with alert rules"
```

---

### Task 7: Grafana 部署

**Files:**
- Create: `k8s/monitoring/grafana-values.yaml`

- [ ] **Step 1: 编写 Grafana Helm values**

```yaml
# k8s/monitoring/grafana-values.yaml
grafana:
  adminPassword: admin123
  service:
    type: NodePort
    nodePort: 30030
  dashboardProviders:
    dashboardproviders.yaml:
      apiVersion: 1
      providers:
        - name: default
          orgId: 1
          folder: ""
          type: file
          disableDeletion: false
          editable: true
          options:
            path: /var/lib/grafana/dashboards/default
  dashboards:
    default:
      demo-services:
        json: |
          {
            "title": "Demo Services Overview",
            "uid": "demo-services",
            "panels": [
              {
                "title": "CPU Usage",
                "type": "graph",
                "targets": [
                  {
                    "expr": "rate(process_cpu_usage[1m]) * 100",
                    "legendFormat": "{{service}}"
                  }
                ]
              },
              {
                "title": "Request Rate",
                "type": "graph",
                "targets": [
                  {
                    "expr": "rate(http_server_requests_seconds_count[1m])",
                    "legendFormat": "{{service}} - {{status}}"
                  }
                ]
              },
              {
                "title": "Error Rate",
                "type": "graph",
                "targets": [
                  {
                    "expr": "rate(http_server_requests_seconds_count{status=~\"5..\"}[1m]) / rate(http_server_requests_seconds_count[1m])",
                    "legendFormat": "{{service}}"
                  }
                ]
              }
            ]
          }
```

- [ ] **Step 2: 由于 kube-prometheus-stack 已包含 Grafana，只需确认运行状态**

```bash
# kube-prometheus-stack 已安装 Grafana，无需单独安装
# 验证 Grafana 可访问
kubectl get pods -n monitoring | grep grafana
kubectl port-forward -n monitoring svc/prometheus-grafana 8080:80 &
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080
# 预期: 200
```

- [ ] **Step 3: Commit**

```bash
git add k8s/monitoring/grafana-values.yaml
git commit -m "feat: add Grafana dashboard for demo services"
```

---

### Task 8: Alertmanager Webhook 配置

**Files:**
- 已在 Task 6 的 prometheus-values.yaml 中配置，此处验证链路

- [ ] **Step 1: 验证 Alertmanager 配置已生效**

```bash
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093 &
curl -s http://localhost:9093/api/v2/status | python -m json.tool | grep webhook
# 预期: 能看到 "ops-agent-webhook" receiver
```

- [ ] **Step 2: 无新文件，skip commit（告警规则和 webhook 已在 Task 6 中配置）**

---

### Task 9: Loki + Promtail 部署

**Files:**
- Create: `k8s/monitoring/loki-values.yaml`

- [ ] **Step 1: 编写 Loki Helm values**

```yaml
# k8s/monitoring/loki-values.yaml
loki:
  auth_enabled: false
  commonConfig:
    replication_factor: 1
  storage:
    type: filesystem
  schemaConfig:
    configs:
      - from: "2024-01-01"
        store: tsdb
        index:
          prefix: loki_index_
          period: 24h
        object_store: filesystem

promtail:
  config:
    clients:
      - url: http://loki:3100/loki/api/v1/push
    snippets:
      pipelineStages:
        - cri: {}
        - match:
            selector: '{app=~"frontend|order|payment|inventory"}'
            stages:
              - json:
                  expressions:
                    level: level
                    message: message
              - labels:
                  level:
                  service: app
```

- [ ] **Step 2: 安装 Loki**

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
helm install loki grafana/loki-stack \
  -f k8s/monitoring/loki-values.yaml \
  --namespace monitoring
kubectl get pods -n monitoring | grep loki
# 预期: loki-0 Running, loki-promtail-* Running
```

- [ ] **Step 3: 验证 Loki 可查询**

```bash
kubectl port-forward -n monitoring svc/loki 3100:3100 &
curl -s "http://localhost:3100/ready"
# 预期: Ready
```

- [ ] **Step 4: Commit**

```bash
git add k8s/monitoring/loki-values.yaml
git commit -m "feat: add Loki + Promtail for log aggregation"
```

---

### Task 10: Spring Boot 多模块项目

**Files:**
- Create: `demo-services/pom.xml`
- Create: `demo-services/common/pom.xml`
- Create: `demo-services/common/src/main/java/com/opsagent/common/FaultController.java`
- Create: `demo-services/frontend-service/pom.xml`
- Create: `demo-services/frontend-service/src/main/java/com/opsagent/frontend/FrontendApplication.java`
- Create: `demo-services/frontend-service/src/main/resources/application.yml`
- Create: `demo-services/order-service/pom.xml`
- Create: `demo-services/order-service/src/main/java/com/opsagent/order/OrderApplication.java`
- Create: `demo-services/order-service/src/main/resources/application.yml`
- Create: `demo-services/payment-service/pom.xml`
- Create: `demo-services/payment-service/src/main/java/com/opsagent/payment/PaymentApplication.java`
- Create: `demo-services/payment-service/src/main/resources/application.yml`
- Create: `demo-services/inventory-service/pom.xml`
- Create: `demo-services/inventory-service/src/main/java/com/opsagent/inventory/InventoryApplication.java`
- Create: `demo-services/inventory-service/src/main/resources/application.yml`

- [ ] **Step 1: 编写父 POM**

```xml
<!-- demo-services/pom.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.3.0</version>
    </parent>

    <groupId>com.opsagent</groupId>
    <artifactId>demo-services</artifactId>
    <version>1.0.0</version>
    <packaging>pom</packaging>

    <modules>
        <module>common</module>
        <module>frontend-service</module>
        <module>order-service</module>
        <module>payment-service</module>
        <module>inventory-service</module>
    </modules>

    <properties>
        <java.version>17</java.version>
    </properties>

    <dependencyManagement>
        <dependencies>
            <dependency>
                <groupId>com.opsagent</groupId>
                <artifactId>common</artifactId>
                <version>${project.version}</version>
            </dependency>
        </dependencies>
    </dependencyManagement>
</project>
```

- [ ] **Step 2: 编写 common 模块（故障注入 Controller）**

```xml
<!-- demo-services/common/pom.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>com.opsagent</groupId>
        <artifactId>demo-services</artifactId>
        <version>1.0.0</version>
    </parent>
    <artifactId>common</artifactId>

    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-actuator</artifactId>
        </dependency>
        <dependency>
            <groupId>io.micrometer</groupId>
            <artifactId>micrometer-registry-prometheus</artifactId>
        </dependency>
    </dependencies>
</project>
```

```java
// demo-services/common/src/main/java/com/opsagent/common/FaultController.java
package com.opsagent.common;

import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.List;

@RestController
@RequestMapping("/fault")
public class FaultController {

    private volatile boolean cpuBurning = false;
    private final List<byte[]> memoryLeak = new ArrayList<>();
    private volatile boolean errorMode = false;
    private volatile long latencyMs = 0;

    @PostMapping("/cpu")
    public String cpu(@RequestParam(defaultValue = "true") boolean enable) {
        cpuBurning = enable;
        if (enable) {
            new Thread(() -> {
                while (cpuBurning) {
                    // busy loop to burn CPU
                }
            }).start();
        }
        return "CPU burn: " + enable;
    }

    @PostMapping("/memory")
    public String memory(@RequestParam(defaultValue = "10") int mb) {
        for (int i = 0; i < mb; i++) {
            memoryLeak.add(new byte[1024 * 1024]);
        }
        return "Allocated " + mb + " MB, total: " + memoryLeak.size() + " MB";
    }

    @PostMapping("/error")
    public String error(@RequestParam(defaultValue = "true") boolean enable) {
        errorMode = enable;
        return "Error mode: " + enable;
    }

    @PostMapping("/latency")
    public String latency(@RequestParam(defaultValue = "5000") long ms) {
        latencyMs = ms;
        return "Latency set to " + ms + "ms";
    }

    @PostMapping("/reset")
    public String reset() {
        cpuBurning = false;
        memoryLeak.clear();
        errorMode = false;
        latencyMs = 0;
        return "All faults reset";
    }

    @GetMapping("/status")
    public String status() {
        return String.format(
            "cpu=%s, memory=%dMB, error=%s, latency=%dms",
            cpuBurning, memoryLeak.size(), errorMode, latencyMs
        );
    }

    // Internal getters for filters
    public boolean isErrorMode() { return errorMode; }
    public long getLatencyMs() { return latencyMs; }
}
```

- [ ] **Step 3: 编写 order-service（作为各服务的模板）**

```xml
<!-- demo-services/order-service/pom.xml -->
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>com.opsagent</groupId>
        <artifactId>demo-services</artifactId>
        <version>1.0.0</version>
    </parent>
    <artifactId>order-service</artifactId>

    <dependencies>
        <dependency>
            <groupId>com.opsagent</groupId>
            <artifactId>common</artifactId>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>
</project>
```

```java
// demo-services/order-service/src/main/java/com/opsagent/order/OrderApplication.java
package com.opsagent.order;

import com.opsagent.common.FaultController;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.web.filter.OncePerRequestFilter;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;

@SpringBootApplication
@Import(FaultController.class)
public class OrderApplication {

    public static void main(String[] args) {
        SpringApplication.run(OrderApplication.class, args);
    }

    @Bean
    public OncePerRequestFilter faultFilter(FaultController faultController) {
        return new OncePerRequestFilter() {
            @Override
            protected void doFilterInternal(
                    HttpServletRequest request,
                    HttpServletResponse response,
                    FilterChain chain) throws ServletException, IOException {

                if (faultController.isErrorMode()
                        && !request.getRequestURI().startsWith("/fault")
                        && !request.getRequestURI().startsWith("/actuator")) {
                    response.sendError(500, "Fault injected error");
                    return;
                }

                long latency = faultController.getLatencyMs();
                if (latency > 0
                        && !request.getRequestURI().startsWith("/fault")
                        && !request.getRequestURI().startsWith("/actuator")) {
                    try {
                        Thread.sleep(latency);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                    }
                }

                chain.doFilter(request, response);
            }
        };
    }
}
```

```yaml
# demo-services/order-service/src/main/resources/application.yml
server:
  port: 8081

spring:
  application:
    name: order-service

management:
  endpoints:
    web:
      exposure:
        include: health,prometheus,metrics
  metrics:
    tags:
      service: order-service
```

- [ ] **Step 4: 按相同模式创建 frontend-service (port 8080), payment-service (port 8082), inventory-service (port 8083)**

结构与 order-service 一致，仅替换端口和应用名。

- [ ] **Step 5: 构建验证**

```bash
cd demo-services
mvn clean compile
# 预期: BUILD SUCCESS
```

- [ ] **Step 6: Commit**

```bash
git add demo-services/
git commit -m "feat: add Spring Boot demo services (frontend, order, payment, inventory) with fault injection"
```

---

### Task 11: 故障注入接口

**Files:**
- 已在 Task 10 的 common 模块中实现（FaultController.java）
- 此任务验证故障注入功能

- [ ] **Step 1: 本地启动 order-service 验证故障注入**

```bash
cd demo-services/order-service
mvn spring-boot:run

# 另一个终端
# 测试正常请求
curl http://localhost:8081/actuator/health
# 预期: {"status":"UP"}

# 触发 CPU 故障
curl -X POST "http://localhost:8081/fault/cpu?enable=true"
# 预期: CPU burn: true

# 触发错误模式
curl -X POST "http://localhost:8081/fault/error?enable=true"
curl http://localhost:8081/actuator/health
# 预期: 500 (因为 /actuator 不在排除列表中... wait, we excluded /actuator)

# 测试业务接口
curl -X POST "http://localhost:8081/fault/latency?ms=3000"
time curl http://localhost:8081/fault/status
# 预期: 耗时约3秒

# 重置
curl -X POST http://localhost:8081/fault/reset
```

- [ ] **Step 2: 无需单独 commit（功能已在 Task 10 中实现）**

---

### Task 12: Dockerfile + Jib 构建

**Files:**
- Create: `demo-services/Dockerfile`
- Create: `demo-services/common/src/main/java/com/opsagent/common/HealthController.java`

- [ ] **Step 1: 编写多阶段 Dockerfile**

```dockerfile
# demo-services/Dockerfile
FROM eclipse-temurin:17-jdk AS builder
WORKDIR /app
COPY . .
RUN ./mvnw clean package -DskipTests

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=builder /app/*/target/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
```

- [ ] **Step 2: 为每个服务编写独立的 Dockerfile（使用不同端口）**

对于每个服务，使用 `--server.port` 参数覆盖端口：
- frontend-service: 8080
- order-service: 8081
- payment-service: 8082
- inventory-service: 8083

- [ ] **Step 3: 构建镜像并加载到 kind**

```bash
cd demo-services

# 构建各服务镜像
for svc in frontend order payment inventory; do
  docker build -t demo-${svc}:latest \
    --build-arg SERVICE=${svc}-service \
    -f ${svc}-service/Dockerfile .
done

# 加载到 kind
for svc in frontend order payment inventory; do
  kind load docker-image demo-${svc}:latest --name ops-agent
done
```

- [ ] **Step 4: Commit**

```bash
git add demo-services/Dockerfile demo-services/*/Dockerfile
git commit -m "feat: add Dockerfiles for Spring Boot demo services"
```

---

### Task 13: 样例业务 K8S 部署

**Files:**
- Create: `k8s/demo-services/namespace.yaml`
- Create: `k8s/demo-services/frontend-deployment.yaml`
- Create: `k8s/demo-services/order-deployment.yaml`
- Create: `k8s/demo-services/payment-deployment.yaml`
- Create: `k8s/demo-services/inventory-deployment.yaml`

- [ ] **Step 1: 编写 namespace**

```yaml
# k8s/demo-services/namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: demo
```

- [ ] **Step 2: 编写 order-service Deployment + Service + ServiceMonitor**

```yaml
# k8s/demo-services/order-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
  namespace: demo
  labels:
    app: order-service
spec:
  replicas: 2
  selector:
    matchLabels:
      app: order-service
  template:
    metadata:
      labels:
        app: order-service
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8081"
        prometheus.io/path: "/actuator/prometheus"
    spec:
      containers:
        - name: order-service
          image: demo-order:latest
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8081
          resources:
            requests:
              cpu: "100m"
              memory: "256Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
          livenessProbe:
            httpGet:
              path: /actuator/health
              port: 8081
            initialDelaySeconds: 30
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /actuator/health
              port: 8081
            initialDelaySeconds: 10
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: order-service
  namespace: demo
  labels:
    app: order-service
spec:
  selector:
    app: order-service
  ports:
    - port: 8081
      targetPort: 8081
      name: http
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: order-service
  namespace: demo
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      app: order-service
  endpoints:
    - port: http
      path: /actuator/prometheus
      interval: 15s
```

- [ ] **Step 3: 按相同模式创建 frontend（8080）、payment（8082）、inventory（8083）的部署清单**

- [ ] **Step 4: 部署到 kind**

```bash
kubectl apply -f k8s/demo-services/namespace.yaml
kubectl apply -f k8s/demo-services/

kubectl get pods -n demo
# 预期: 每个服务 2 个副本，共 8 个 Pod，全部 Running

kubectl get servicemonitors -n demo
# 预期: 4 个 ServiceMonitor
```

- [ ] **Step 5: Commit**

```bash
git add k8s/demo-services/
git commit -m "feat: add K8S manifests for demo services with ServiceMonitor"
```

---

### Task 14: LangGraph Workflow 定义

**Files:**
- Create: `agent/workflows/alert_workflow.py`
- Create: `agent/workflows/__init__.py`

- [ ] **Step 1: 编写状态定义和 Workflow**

```python
# agent/workflows/alert_workflow.py
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

from agent.db.models import Incident


class AlertState(TypedDict):
    alert_raw: dict
    incident_id: Optional[str]
    alert_parsed: Optional[dict]
    context: Optional[dict]
    diagnosis: Optional[dict]
    error: Optional[str]


def parse_alert(state: AlertState) -> AlertState:
    """Node: 解析告警 → 创建 Incident"""
    from agent.agents.alert import parse_and_create_incident
    return parse_and_create_incident(state)


def collect_context(state: AlertState) -> AlertState:
    """Node: 收集上下文"""
    from agent.agents.supervisor import collect_context_for_incident
    return collect_context_for_incident(state)


def diagnose(state: AlertState) -> AlertState:
    """Node: 根因分析"""
    from agent.agents.rca import analyze_root_cause
    return analyze_root_cause(state)


def should_continue(state: AlertState) -> str:
    if state.get("error"):
        return END
    if state.get("diagnosis"):
        return END
    if state.get("context"):
        return "diagnose"
    if state.get("incident_id"):
        return "collect_context"
    return "parse_alert"


def build_alert_workflow() -> StateGraph:
    workflow = StateGraph(AlertState)

    workflow.add_node("parse_alert", parse_alert)
    workflow.add_node("collect_context", collect_context)
    workflow.add_node("diagnose", diagnose)

    workflow.set_entry_point("parse_alert")
    workflow.add_conditional_edges("parse_alert", should_continue, {
        "collect_context": "collect_context",
        END: END,
    })
    workflow.add_conditional_edges("collect_context", should_continue, {
        "diagnose": "diagnose",
        END: END,
    })
    workflow.add_edge("diagnose", END)

    return workflow.compile()
```

- [ ] **Step 2: Commit**

```bash
git add agent/workflows/
git commit -m "feat: add LangGraph alert workflow (Alert → Context → RCA)"
```

---

### Task 15: Supervisor Agent

**Files:**
- Create: `agent/agents/__init__.py`
- Create: `agent/agents/supervisor.py`

- [ ] **Step 1: 编写 Supervisor Agent**

```python
# agent/agents/supervisor.py
import logging
from agent.workflows.alert_workflow import AlertState, build_alert_workflow

logger = logging.getLogger("ops-agent.supervisor")


async def run_alert_workflow(alert_raw: dict) -> dict:
    """编排告警处理全流程，返回最终诊断结果"""
    workflow = build_alert_workflow()

    initial_state: AlertState = {
        "alert_raw": alert_raw,
        "incident_id": None,
        "alert_parsed": None,
        "context": None,
        "diagnosis": None,
        "error": None,
    }

    result = await workflow.ainvoke(initial_state)
    logger.info(f"Workflow completed for incident: {result.get('incident_id')}")
    return result


async def collect_context_for_incident(state: AlertState) -> AlertState:
    """收集上下文数据"""
    from agent.tools.prometheus import query_service_metrics
    from agent.tools.loki import query_service_logs
    from agent.tools.kubernetes import get_service_pods
    from agent.tools.cmdb import get_service_info

    service = state["alert_parsed"]["service"]
    env = state["alert_parsed"].get("env", "prod")

    try:
        metrics = await query_service_metrics(service)
        logs = await query_service_logs(service)
        pods = await get_service_pods(service, namespace=f"demo")
        cmdb_info = await get_service_info(service)

        state["context"] = {
            "metrics": metrics,
            "logs": logs,
            "pods": pods,
            "cmdb": cmdb_info,
        }
        logger.info(f"Context collected for {service}: {len(metrics)} metrics, {len(logs)} log entries")
    except Exception as e:
        logger.error(f"Context collection failed: {e}")
        state["error"] = str(e)

    return state
```

- [ ] **Step 2: Commit**

```bash
git add agent/agents/__init__.py agent/agents/supervisor.py
git commit -m "feat: add Supervisor Agent for workflow orchestration"
```

---

### Task 16: SQLAlchemy ORM + CRUD

**Files:**
- Create: `agent/db/__init__.py`
- Create: `agent/db/models.py`
- Create: `agent/db/crud.py`

- [ ] **Step 1: 编写 ORM 模型**

```python
# agent/db/models.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Float, Text, DateTime, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


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
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Execution(Base):
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(64), ForeignKey("incidents.id"))
    action = Column(String(128), nullable=False)
    operator = Column(String(64))
    status = Column(String(32), nullable=False, default="pending")
    result = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True))


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(64), ForeignKey("incidents.id"))
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    incident_id = Column(String(64))
    actor = Column(String(64), nullable=False)
    action = Column(String(128), nullable=False)
    detail = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 2: 编写 CRUD 操作**

```python
# agent/db/crud.py
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select

from agent.config import settings
from agent.db.models import Incident, Execution, Report, AuditLog

engine = create_async_engine(settings.database_url)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def create_incident(session: AsyncSession, incident: Incident) -> Incident:
    session.add(incident)
    await session.commit()
    await session.refresh(incident)
    return incident


async def get_incident(session: AsyncSession, incident_id: str) -> Incident | None:
    result = await session.execute(
        select(Incident).where(Incident.id == incident_id)
    )
    return result.scalar_one_or_none()


async def update_incident(session: AsyncSession, incident_id: str, **kwargs) -> Incident | None:
    incident = await get_incident(session, incident_id)
    if incident:
        for key, value in kwargs.items():
            setattr(incident, key, value)
        await session.commit()
        await session.refresh(incident)
    return incident


async def list_incidents(session: AsyncSession, status: str = None, limit: int = 50) -> list[Incident]:
    stmt = select(Incident).order_by(Incident.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Incident.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def create_audit_log(session: AsyncSession, log: AuditLog) -> AuditLog:
    session.add(log)
    await session.commit()
    return log
```

- [ ] **Step 3: 验证数据库连接**

```python
# 在 agent/ 目录下运行
python -c "
import asyncio
from agent.db.crud import engine, AsyncSessionLocal
from sqlalchemy import text

async def test():
    async with AsyncSessionLocal() as s:
        result = await s.execute(text('SELECT 1'))
        print('DB OK:', result.scalar())

asyncio.run(test())
"
# 预期: DB OK: 1
```

- [ ] **Step 4: Commit**

```bash
git add agent/db/
git commit -m "feat: add SQLAlchemy ORM models and CRUD operations"
```

---

### Task 17: LLM 适配层

**Files:**
- Create: `agent/llm/__init__.py`
- Create: `agent/llm/client.py`

- [ ] **Step 1: 编写 LLM 客户端**

```python
# agent/llm/client.py
from openai import AsyncOpenAI

from agent.config import settings

client = AsyncOpenAI(
    base_url=settings.deepseek_base_url,
    api_key=settings.deepseek_api_key,
)


async def chat(prompt: str, system: str = None, model: str = None) -> str:
    """发送单轮对话，返回文本响应"""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = await client.chat.completions.create(
        model=model or settings.deepseek_model,
        messages=messages,
        temperature=0.1,
    )
    return response.choices[0].message.content


async def chat_json(prompt: str, system: str = None, model: str = None) -> dict:
    """发送对话，要求返回 JSON 格式"""
    system_msg = (system or "") + "\nYou MUST respond with valid JSON only, no markdown, no explanation."
    text = await chat(prompt, system=system_msg, model=model)
    import json
    # 处理可能的 markdown code block
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])
    return json.loads(text)
```

- [ ] **Step 2: 验证 LLM 连接**

```bash
# 确保 .env 中 DEEPSEEK_API_KEY 已配置
python -c "
import asyncio
from agent.llm.client import chat

async def test():
    r = await chat('Say hello in one word')
    print('LLM response:', r)

asyncio.run(test())
"
# 预期: LLM response: Hello (或类似简短回复)
```

- [ ] **Step 3: Commit**

```bash
git add agent/llm/
git commit -m "feat: add LLM adapter for DeepSeek-V4-Flash (OpenAI-compatible API)"
```

---

### Task 18: Webhook API

**Files:**
- Create: `agent/api/__init__.py`
- Create: `agent/api/v1/__init__.py`
- Create: `agent/api/v1/alerts.py`

- [ ] **Step 1: 编写 Webhook 接收端点**

```python
# agent/api/v1/alerts.py
import logging
from fastapi import APIRouter, Request, BackgroundTasks

from agent.workflows.alert_workflow import AlertState, build_alert_workflow

logger = logging.getLogger("ops-agent.api.alerts")
router = APIRouter(prefix="/api/v1")


@router.post("/alerts")
async def receive_alert(request: Request, background_tasks: BackgroundTasks):
    """接收 Alertmanager Webhook 回调"""
    body = await request.json()
    logger.info(f"Received alert webhook: {body.get('receiver', 'unknown')}")

    alerts = body.get("alerts", [])
    if not alerts:
        return {"status": "no_alerts"}

    results = []
    for alert in alerts:
        # 将诊断放到后台任务中执行，立即返回 200 给 Alertmanager
        alert_data = {
            "alertname": alert.get("labels", {}).get("alertname", "unknown"),
            "service": alert.get("labels", {}).get("service", alert.get("annotations", {}).get("service", "unknown")),
            "env": alert.get("labels", {}).get("env", "prod"),
            "severity": alert.get("labels", {}).get("severity", "P3"),
            "value": alert.get("annotations", {}).get("value", alert.get("annotations", {}).get("summary", "")),
            "starts_at": alert.get("startsAt", ""),
            "fingerprint": alert.get("fingerprint", ""),
        }
        background_tasks.add_task(run_diagnosis, alert_data)
        results.append({"alertname": alert_data["alertname"], "status": "accepted"})

    return {"status": "ok", "alerts": len(results), "results": results}


async def run_diagnosis(alert_data: dict):
    """后台运行诊断流程"""
    workflow = build_alert_workflow()
    state: AlertState = {
        "alert_raw": alert_data,
        "incident_id": None,
        "alert_parsed": None,
        "context": None,
        "diagnosis": None,
        "error": None,
    }
    try:
        result = await workflow.ainvoke(state)
        logger.info(f"Diagnosis completed for {alert_data.get('service')}: incident={result.get('incident_id')}")
    except Exception as e:
        logger.error(f"Diagnosis failed: {e}", exc_info=True)
```

- [ ] **Step 2: 注册路由到 main.py**

```python
# agent/main.py 增加:
from agent.api.v1 import alerts

app.include_router(alerts.router)
```

- [ ] **Step 3: Commit**

```bash
git add agent/api/ agent/main.py
git commit -m "feat: add Alertmanager webhook API endpoint"
```

---

### Task 19: 告警去重与聚合

**Files:**
- Create: `agent/agents/alert.py`

- [ ] **Step 1: 编写 Alert Agent**

```python
# agent/agents/alert.py
import logging
import redis.asyncio as aioredis
from datetime import datetime, timezone

from agent.config import settings
from agent.db.models import Incident
from agent.db.crud import create_incident, AsyncSessionLocal
from agent.workflows.alert_workflow import AlertState

logger = logging.getLogger("ops-agent.alert")


async def _get_redis():
    return aioredis.from_url(f"redis://{settings.redis_host}:{settings.redis_port}")


async def parse_and_create_incident(state: AlertState) -> AlertState:
    """解析告警，去重后创建 Incident"""
    alert = state["alert_raw"]
    redis = await _get_redis()

    try:
        fingerprint = alert.get("fingerprint", "")
        dedup_key = f"alert:dedup:{fingerprint}"

        # 检查 5 分钟内是否已处理
        existing = await redis.get(dedup_key)
        if existing:
            logger.info(f"Duplicate alert skipped: {alert.get('alertname')} (fingerprint={fingerprint})")
            state["incident_id"] = existing.decode()
            state["alert_parsed"] = alert
            return state

        # 设置去重缓存（5 分钟过期）
        await redis.setex(dedup_key, settings.alert_dedup_window, "")

        # 创建 Incident
        async with AsyncSessionLocal() as session:
            incident = Incident(
                service=alert.get("service", "unknown"),
                env=alert.get("env", "prod"),
                severity=alert.get("severity", "P3"),
                alert_name=alert.get("alertname"),
                alert_value=alert.get("value"),
                status="diagnosing",
            )
            incident = await create_incident(session, incident)

            # 更新去重缓存为实际 incident_id
            await redis.setex(dedup_key, settings.alert_dedup_window, incident.id)

            state["incident_id"] = incident.id
            state["alert_parsed"] = alert
            logger.info(f"Incident created: {incident.id} for {incident.service}")

    except Exception as e:
        logger.error(f"Failed to parse alert: {e}", exc_info=True)
        state["error"] = str(e)
    finally:
        await redis.aclose()

    return state
```

- [ ] **Step 2: Commit**

```bash
git add agent/agents/alert.py
git commit -m "feat: add Alert Agent with Redis-based deduplication"
```

---

### Task 20: 飞书 Bot 基础接入

**Files:**
- Create: `agent/channels/__init__.py`
- Create: `agent/channels/feishu.py`

- [ ] **Step 1: 编写飞书 SDK 封装**

```python
# agent/channels/feishu.py
import logging
import httpx

from agent.config import settings

logger = logging.getLogger("ops-agent.feishu")

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
FEISHU_AUTH_URL = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
FEISHU_MESSAGE_URL = f"{FEISHU_API_BASE}/im/v1/messages"
FEISHU_CHAT_URL = f"{FEISHU_API_BASE}/im/v1/chats"

_token_cache: dict = {"token": None, "expires_at": 0}


async def _get_tenant_access_token() -> str:
    import time
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            FEISHU_AUTH_URL,
            json={
                "app_id": settings.feishu_app_id,
                "app_secret": settings.feishu_app_secret,
            },
        )
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"Feishu auth failed: {data}")

        _token_cache["token"] = data["tenant_access_token"]
        _token_cache["expires_at"] = now + data.get("expire", 7200)
        return _token_cache["token"]


async def send_message(receive_id_type: str, receive_id: str, content: dict) -> dict:
    """发送飞书消息

    Args:
        receive_id_type: 'open_id', 'user_id', 'chat_id' 等
        receive_id: 接收者 ID
        content: 消息内容（飞书消息 JSON 格式）
    """
    token = await _get_tenant_access_token()
    body = {
        "receive_id": receive_id,
        "msg_type": content.get("msg_type", "interactive"),
        "content": content.get("content", "{}"),
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{FEISHU_MESSAGE_URL}?receive_id_type={receive_id_type}",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        result = resp.json()
        if result.get("code") != 0:
            logger.error(f"Feishu send failed: {result}")
        return result


async def send_card_to_chat(chat_id: str, card: dict) -> dict:
    """发送交互式卡片消息到飞书群"""
    import json
    return await send_message("chat_id", chat_id, {
        "msg_type": "interactive",
        "content": json.dumps(card),
    })


async def update_card(message_id: str, card: dict) -> dict:
    """更新已发送的卡片消息"""
    token = await _get_tenant_access_token()
    import json

    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{FEISHU_API_BASE}/im/v1/messages/{message_id}",
            json={"content": json.dumps(card)},
            headers={"Authorization": f"Bearer {token}"},
        )
        return resp.json()
```

- [ ] **Step 2: Commit**

```bash
git add agent/channels/
git commit -m "feat: add Feishu Open API SDK (auth, send message, update card)"
```

---

### Task 21: 飞书消息卡片模板

**Files:**
- Create: `agent/templates/__init__.py`
- Create: `agent/templates/cards/alert_card.json`
- Create: `agent/templates/cards/diagnosis_card.json`

- [ ] **Step 1: 编写告警通知卡片模板**

```json
{
  "config": { "wide_screen_mode": true },
  "header": {
    "title": { "tag": "plain_text", "content": "{{alert_title}}" },
    "template": "{{severity_color}}"
  },
  "elements": [
    {
      "tag": "div",
      "fields": [
        { "is_short": true, "text": { "tag": "lark_md", "content": "**服务**\n{{service}}" } },
        { "is_short": true, "text": { "tag": "lark_md", "content": "**环境**\n{{env}}" } },
        { "is_short": true, "text": { "tag": "lark_md", "content": "**级别**\n{{severity}}" } },
        { "is_short": true, "text": { "tag": "lark_md", "content": "**当前值**\n{{value}}" } }
      ]
    },
    { "tag": "hr" },
    {
      "tag": "note",
      "elements": [
        { "tag": "plain_text", "content": "事件编号: {{incident_id}} | 状态: Diagnosing | Agent 已开始自动诊断..." }
      ]
    }
  ]
}
```

- [ ] **Step 2: 编写诊断结果卡片模板**

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
      "content": "**证据：**\n{{evidence_list}}"
    },
    { "tag": "hr" },
    {
      "tag": "markdown",
      "content": "**置信度：** {{confidence}}%"
    },
    { "tag": "hr" },
    {
      "tag": "note",
      "elements": [
        { "tag": "plain_text", "content": "事件编号: {{incident_id}} | 状态: {{status}} | 耗时: {{duration}}" }
      ]
    }
  ]
}
```

- [ ] **Step 3: 编写卡片渲染工具**

```python
# agent/templates/__init__.py
import json
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "cards"


def render_card(template_name: str, **kwargs) -> dict:
    """从模板文件渲染飞书卡片"""
    with open(_TEMPLATE_DIR / f"{template_name}.json") as f:
        template_text = f.read()

    for key, value in kwargs.items():
        template_text = template_text.replace(f"{{{{{key}}}}}", str(value))

    return json.loads(template_text)
```

- [ ] **Step 4: Commit**

```bash
git add agent/templates/
git commit -m "feat: add Feishu card templates (alert notification + diagnosis result)"
```

---

### Task 22: 告警→飞书推送

**Files:**
- Modify: `agent/agents/alert.py`

- [ ] **Step 1: 在 Alert Agent 中集成飞书推送**

在 `parse_and_create_incident` 函数末尾，创建 Incident 成功后添加飞书推送：

```python
# 在 agent/agents/alert.py 的 parse_and_create_incident 函数末尾，
# 在 "state["alert_parsed"] = alert" 之后添加:

            # 推送到飞书
            await _notify_feishu(incident, alert)


async def _notify_feishu(incident: Incident, alert: dict):
    """推送告警卡片到飞书群"""
    from agent.channels.feishu import send_card_to_chat
    from agent.templates import render_card

    severity_color_map = {
        "P0": "red",
        "P1": "orange",
        "P2": "yellow",
        "P3": "blue",
    }

    try:
        card = render_card(
            "alert_card",
            alert_title=f"[{incident.severity}] {incident.service} - {incident.alert_name}",
            severity_color=severity_color_map.get(incident.severity, "blue"),
            service=incident.service,
            env=incident.env,
            severity=incident.severity,
            value=incident.alert_value or "",
            incident_id=incident.id,
        )
        # TODO: 从 CMDB 获取服务对应的飞书群 chat_id
        chat_id = settings.feishu_bot_webhook  # 临时使用 webhook 配置
        result = await send_card_to_chat(chat_id, card)
        logger.info(f"Feishu notification sent: {result}")
    except Exception as e:
        logger.error(f"Feishu notification failed: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add agent/agents/alert.py
git commit -m "feat: push alert card to Feishu on incident creation"
```

---

### Task 23: Prometheus 查询工具

**Files:**
- Create: `agent/tools/__init__.py`
- Create: `agent/tools/prometheus.py`

- [ ] **Step 1: 编写 Prometheus 查询封装**

```python
# agent/tools/prometheus.py
import logging
from datetime import datetime, timedelta
import httpx

from agent.config import settings

logger = logging.getLogger("ops-agent.tools.prometheus")

PROM_API = f"{settings.prometheus_url}/api/v1"


async def _query(promql: str) -> list:
    """执行 PromQL 即时查询"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{PROM_API}/query", params={"query": promql})
        data = resp.json()
        if data["status"] != "success":
            logger.warning(f"PromQL failed: {promql} -> {data.get('error')}")
            return []
        return data["data"]["result"]


async def query_service_metrics(service: str) -> dict:
    """查询服务的核心指标"""
    now = datetime.utcnow()
    five_min_ago = now - timedelta(minutes=5)
    step = "15s"

    queries = {
        "cpu": f'rate(process_cpu_usage{{service="{service}"}}[5m]) * 100',
        "memory": f'jvm_memory_used_bytes{{service="{service}"}}',
        "qps": f'rate(http_server_requests_seconds_count{{service="{service}"}}[5m])',
        "rt_avg": f'rate(http_server_requests_seconds_sum{{service="{service}"}}[5m]) / rate(http_server_requests_seconds_count{{service="{service}"}}[5m])',
        "error_rate": f'rate(http_server_requests_seconds_count{{service="{service}",status=~"5.."}}[5m]) / rate(http_server_requests_seconds_count{{service="{service}"}}[5m])',
    }

    results = {}
    for name, promql in queries.items():
        try:
            data = await _query(promql)
            results[name] = {
                "current": float(data[0]["value"][1]) if data else 0,
                "samples": len(data),
            }
        except Exception as e:
            logger.error(f"Metric query failed [{name}]: {e}")
            results[name] = {"current": 0, "error": str(e)}

    logger.info(f"Metrics for {service}: CPU={results.get('cpu',{}).get('current',0):.1f}%, QPS={results.get('qps',{}).get('current',0):.1f}")
    return results
```

- [ ] **Step 2: Commit**

```bash
git add agent/tools/__init__.py agent/tools/prometheus.py
git commit -m "feat: add Prometheus query tools (CPU, Mem, QPS, RT, ErrorRate)"
```

---

### Task 24: Loki 日志查询工具

**Files:**
- Create: `agent/tools/loki.py`

- [ ] **Step 1: 编写 Loki 查询封装**

```python
# agent/tools/loki.py
import logging
from datetime import datetime, timedelta
import httpx

from agent.config import settings

logger = logging.getLogger("ops-agent.tools.loki")

LOKI_API = f"{settings.loki_url}/loki/api/v1"


async def query_service_logs(service: str, keyword: str = "ERROR", minutes: int = 10) -> list:
    """查询指定服务的日志"""
    query = f'{{app="{service}"}} |= "{keyword}"'

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{LOKI_API}/query_range",
            params={
                "query": query,
                "limit": 50,
                "start": int((datetime.utcnow() - timedelta(minutes=minutes)).timestamp() * 1e9),
                "end": int(datetime.utcnow().timestamp() * 1e9),
            },
        )
        data = resp.json()
        if data.get("status") != "success":
            logger.warning(f"Loki query failed: {query}")
            return []

        results = []
        for stream in data.get("data", {}).get("result", []):
            for ts, line in stream.get("values", []):
                results.append({"timestamp": ts, "line": line, "labels": stream.get("stream", {})})

        logger.info(f"Loki logs for {service}: {len(results)} entries (keyword={keyword})")
        return results


async def count_error_logs(service: str, minutes: int = 10) -> int:
    """统计错误日志数量"""
    logs = await query_service_logs(service, "ERROR", minutes)
    return len(logs)
```

- [ ] **Step 2: Commit**

```bash
git add agent/tools/loki.py
git commit -m "feat: add Loki log query tools"
```

---

### Task 25: Kubernetes 查询工具

**Files:**
- Create: `agent/tools/kubernetes.py`

- [ ] **Step 1: 编写 K8S 查询封装**

```python
# agent/tools/kubernetes.py
import logging
import httpx

logger = logging.getLogger("ops-agent.tools.k8s")

# 通过 kubectl proxy 访问 K8S API
K8S_API = "http://localhost:8001"


async def get_service_pods(service: str, namespace: str = "demo") -> dict:
    """获取服务的 Pod 状态"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{K8S_API}/api/v1/namespaces/{namespace}/pods",
            params={"labelSelector": f"app={service}"},
        )
        if resp.status_code != 200:
            return {"error": f"k8s api error: {resp.status_code}"}

        data = resp.json()
        pods = []
        for item in data.get("items", []):
            status = item.get("status", {})
            container_statuses = status.get("containerStatuses", [])
            pods.append({
                "name": item["metadata"]["name"],
                "phase": status.get("phase"),
                "ready": all(cs.get("ready", False) for cs in container_statuses),
                "restarts": sum(cs.get("restartCount", 0) for cs in container_statuses),
                "node": item["spec"].get("nodeName"),
            })

        return {
            "total": len(pods),
            "ready": sum(1 for p in pods if p["ready"]),
            "pods": pods,
        }


async def get_pod_events(service: str, namespace: str = "demo") -> list:
    """获取服务相关 K8S Events"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{K8S_API}/api/v1/namespaces/{namespace}/events",
            params={"fieldSelector": f"involvedObject.name~={service}"},
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        events = []
        for item in data.get("items", []):
            events.append({
                "type": item.get("type"),
                "reason": item.get("reason"),
                "message": item.get("message"),
                "timestamp": item.get("lastTimestamp") or item["metadata"]["creationTimestamp"],
            })
        return events
```

- [ ] **Step 2: Commit**

```bash
git add agent/tools/kubernetes.py
git commit -m "feat: add Kubernetes API query tools (pods, events)"
```

---

### Task 26: CMDB 查询工具（Mock）

**Files:**
- Create: `agent/tools/cmdb.py`

- [ ] **Step 1: 编写 Mock CMDB**

```python
# agent/tools/cmdb.py
import logging

logger = logging.getLogger("ops-agent.tools.cmdb")

MOCK_CMDB = {
    "frontend-service": {
        "owner": "张三",
        "team": "前端组",
        "dependencies": ["order-service"],
        "oncall": "张三",
        "chat_id": "oc_chat_frontend",
    },
    "order-service": {
        "owner": "李四",
        "team": "订单组",
        "dependencies": ["payment-service", "inventory-service", "redis", "postgres"],
        "oncall": "李四",
        "chat_id": "oc_chat_order",
    },
    "payment-service": {
        "owner": "王五",
        "team": "支付组",
        "dependencies": ["redis", "postgres"],
        "oncall": "王五",
        "chat_id": "oc_chat_payment",
    },
    "inventory-service": {
        "owner": "赵六",
        "team": "库存组",
        "dependencies": ["redis", "postgres"],
        "oncall": "赵六",
        "chat_id": "oc_chat_inventory",
    },
}


async def get_service_info(service: str) -> dict:
    """获取服务 CMDB 信息"""
    info = MOCK_CMDB.get(service, {})
    logger.info(f"CMDB lookup: {service} -> owner={info.get('owner', 'unknown')}")
    return info


async def get_service_owner(service: str) -> str:
    info = await get_service_info(service)
    return info.get("owner", "unknown")


async def get_service_dependencies(service: str) -> list:
    info = await get_service_info(service)
    return info.get("dependencies", [])


async def get_service_chat_id(service: str) -> str:
    info = await get_service_info(service)
    return info.get("chat_id", "")
```

- [ ] **Step 2: Commit**

```bash
git add agent/tools/cmdb.py
git commit -m "feat: add mock CMDB with service metadata (owner, dependencies, chat_id)"
```

---

### Task 27: RCA Agent - CPU 高诊断

**Files:**
- Create: `agent/agents/rca.py`

- [ ] **Step 1: 编写 RCA Agent**

```python
# agent/agents/rca.py
import logging

from agent.workflows.alert_workflow import AlertState

logger = logging.getLogger("ops-agent.rca")


async def analyze_root_cause(state: AlertState) -> AlertState:
    """分析根因，生成诊断结论"""
    context = state.get("context", {})
    alert = state.get("alert_parsed", {})
    alert_name = alert.get("alertname", "")

    if "CPU" in alert_name.upper() or "cpu" in alert_name.lower():
        diagnosis = _diagnose_cpu_high(context)
    else:
        diagnosis = _diagnose_generic(context, alert)

    state["diagnosis"] = diagnosis

    # 更新数据库
    await _save_diagnosis(state["incident_id"], diagnosis)

    # 推送飞书诊断结果
    await _notify_diagnosis(state["incident_id"], diagnosis, alert)

    return state


def _diagnose_cpu_high(context: dict) -> dict:
    """CPU 高诊断逻辑"""
    metrics = context.get("metrics", {})
    pods = context.get("pods", {})
    cmdb = context.get("cmdb", {})

    cpu = metrics.get("cpu", {}).get("current", 0)
    qps = metrics.get("qps", {}).get("current", 0)
    error_rate = metrics.get("error_rate", {}).get("current", 0)
    rt = metrics.get("rt_avg", {}).get("current", 0)

    evidence = []
    root_cause = ""
    confidence = 0.0

    # 判断逻辑
    all_pods_high = pods.get("total", 0) == pods.get("ready", 0) and pods.get("total", 0) > 0

    if qps > 100 and all_pods_high:
        root_cause = "流量上涨导致服务资源不足"
        confidence = 0.85
        evidence = [
            f"CPU 使用率: {cpu:.1f}%",
            f"QPS 当前值: {qps:.1f}/s，相比基线显著增加",
            "所有 Pod CPU 同时升高" if all_pods_high else "部分 Pod CPU 异常",
            f"RT 当前值: {rt*1000:.0f}ms",
            f"错误率: {error_rate*100:.2f}%",
        ]
    elif not all_pods_high and qps < 50:
        root_cause = "单实例异常或代码死循环"
        confidence = 0.70
        evidence = [
            f"CPU 使用率: {cpu:.1f}%",
            "仅部分 Pod CPU 异常，非全量",
            "QPS 无明显上涨",
            "排除流量原因，建议检查线程栈和最近部署",
        ]
    else:
        root_cause = "CPU 异常升高，需进一步排查"
        confidence = 0.40
        evidence = [
            f"CPU 使用率: {cpu:.1f}%",
            f"QPS: {qps:.1f}/s",
            "证据不足，建议人工介入排查部署记录和节点状态",
        ]

    return {
        "root_cause": root_cause,
        "confidence": confidence,
        "evidence": evidence,
    }


def _diagnose_generic(context: dict, alert: dict) -> dict:
    return {
        "root_cause": f"收到{alert.get('alertname', '未知')}告警，等待扩展诊断能力",
        "confidence": 0.3,
        "evidence": [f"告警名称: {alert.get('alertname')}", f"当前值: {alert.get('value')}"],
    }


async def _save_diagnosis(incident_id: str, diagnosis: dict):
    """将诊断结果写入数据库"""
    from agent.db.crud import update_incident, AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        await update_incident(
            session,
            incident_id,
            root_cause=diagnosis["root_cause"],
            confidence=diagnosis["confidence"],
            evidence=diagnosis["evidence"],
            status="diagnosed",
        )


async def _notify_diagnosis(incident_id: str, diagnosis: dict, alert: dict):
    """推送诊断结果飞书卡片"""
    try:
        from agent.channels.feishu import send_card_to_chat
        from agent.templates import render_card
        from agent.tools.cmdb import get_service_chat_id

        service = alert.get("service", "")
        chat_id = await get_service_chat_id(service)

        evidence_list = "\n".join(f"- {e}" for e in diagnosis["evidence"])
        severity = alert.get("severity", "P3")
        severity_color = {"P0": "red", "P1": "orange", "P2": "yellow"}.get(severity, "blue")

        card = render_card(
            "diagnosis_card",
            alert_title=f"[{severity}] {service} - 诊断完成",
            severity_color=severity_color,
            root_cause=diagnosis["root_cause"],
            evidence_list=evidence_list,
            confidence=f"{diagnosis['confidence']*100:.0f}",
            incident_id=incident_id,
            status="待确认",
            duration="刚刚",
        )
        await send_card_to_chat(chat_id, card)
    except Exception as e:
        logger.error(f"Failed to send diagnosis card: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add agent/agents/rca.py
git commit -m "feat: add RCA Agent with CPU high diagnosis logic and evidence chain"
```

---

### Task 28: Incident API

**Files:**
- Create: `agent/api/v1/incidents.py`

- [ ] **Step 1: 编写 Incident 查询 API**

```python
# agent/api/v1/incidents.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.crud import get_incident, list_incidents, AsyncSessionLocal

router = APIRouter(prefix="/api/v1")


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/incidents")
async def list_incidents_endpoint(status: str = None, limit: int = 50, db: AsyncSession = Depends(get_db)):
    incidents = await list_incidents(db, status=status, limit=limit)
    return {
        "total": len(incidents),
        "incidents": [
            {
                "id": i.id,
                "service": i.service,
                "env": i.env,
                "severity": i.severity,
                "status": i.status,
                "alert_name": i.alert_name,
                "root_cause": i.root_cause,
                "confidence": i.confidence,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in incidents
        ],
    }


@router.get("/incidents/{incident_id}")
async def get_incident_endpoint(incident_id: str, db: AsyncSession = Depends(get_db)):
    incident = await get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    return {
        "id": incident.id,
        "service": incident.service,
        "env": incident.env,
        "severity": incident.severity,
        "status": incident.status,
        "alert_name": incident.alert_name,
        "alert_value": incident.alert_value,
        "root_cause": incident.root_cause,
        "confidence": incident.confidence,
        "evidence": incident.evidence,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
        "updated_at": incident.updated_at.isoformat() if incident.updated_at else None,
    }
```

- [ ] **Step 2: 注册路由**

```python
# agent/main.py 增加:
from agent.api.v1 import incidents
app.include_router(incidents.router)
```

- [ ] **Step 3: Commit**

```bash
git add agent/api/v1/incidents.py agent/main.py
git commit -m "feat: add Incident list/detail API endpoints"
```

---

### Task 29: 端到端联调

**目标**：验证全链路 — 故障注入→Prometheus 告警→Alertmanager→Agent→诊断→飞书卡片推送。

- [ ] **Step 1: 确保所有服务运行**

```bash
# 确认 Docker 环境
docker compose ps
# 预期: postgres (healthy), redis (healthy)

# 确认 K8S 集群
kubectl get nodes --context kind-ops-agent

# 确认 demo 服务
kubectl get pods -n demo
# 预期: 8 Pods Running

# 确认监控栈
kubectl get pods -n monitoring
# 预期: prometheus, alertmanager, grafana, loki 全部 Running

# 启动 Agent
cd agent && uvicorn agent.main:app --host 0.0.0.0 --port 8000 &

# 启动 kubectl proxy (供 K8S 工具查询)
kubectl proxy --port=8001 &
```

- [ ] **Step 2: 触发故障并观察全链路**

```bash
# Port-forward order-service
kubectl port-forward -n demo svc/order-service 8081:8081 &

# 触发 CPU 故障
curl -X POST "http://localhost:8081/fault/cpu?enable=true"

# 等待 Prometheus 产生告警 (1 分钟后)
sleep 90

# 检查 Prometheus 告警
curl -s "http://localhost:9090/api/v1/alerts" | python -m json.tool | grep HighCPU

# 检查 Agent 是否收到并处理
curl -s http://localhost:8000/api/v1/incidents | python -m json.tool

# 检查 Incident 详情（找到最新的 incident_id）
curl -s http://localhost:8000/api/v1/incidents/INC-XXXXXXXXXXXX | python -m json.tool

# 预期: 能看到 root_cause、confidence、evidence
```

- [ ] **Step 3: 验证诊断结果**

```bash
# 诊断结论应包含:
# - root_cause: "流量上涨导致服务资源不足" 或类似
# - confidence: > 0.5
# - evidence: 至少 3 条证据
# - status: "diagnosed"
```

- [ ] **Step 4: Commit 端到端测试脚本**

```bash
# 创建 e2e 测试脚本
cat > tests/e2e_phase1.sh << 'EOF'
#!/bin/bash
set -e

echo "=== Phase 1 E2E Test ==="

echo "[1/5] Triggering CPU fault..."
kubectl exec -n demo deploy/order-service -- curl -s -X POST http://localhost:8081/fault/cpu?enable=true

echo "[2/5] Waiting for Prometheus alert (90s)..."
sleep 90

echo "[3/5] Checking Prometheus alerts..."
ALERTS=$(curl -s "http://localhost:9090/api/v1/alerts" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data']['alerts']))")
echo "Active alerts: $ALERTS"

echo "[4/5] Checking Agent incidents..."
INCIDENTS=$(curl -s http://localhost:8000/api/v1/incidents | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['total'])")
echo "Incidents created: $INCIDENTS"
if [ "$INCIDENTS" -gt 0 ]; then
    echo "PASS: Incident created successfully"
else
    echo "FAIL: No incident created"
    exit 1
fi

echo "[5/5] Checking diagnosis quality..."
DIAGNOSIS=$(curl -s http://localhost:8000/api/v1/incidents | python3 -c "
import sys,json
d=json.load(sys.stdin)
i = d['incidents'][-1]
print(f'root_cause={i[\"root_cause\"]}, confidence={i[\"confidence\"]}')
")
echo "$DIAGNOSIS"

echo "=== E2E Test Complete ==="
EOF
chmod +x tests/e2e_phase1.sh

git add tests/e2e_phase1.sh
git commit -m "feat: add Phase 1 end-to-end test script"
```

---

## Plan Summary

| Task | 内容 | 依赖 |
|------|------|------|
| 1-4 | 基础设施 (Docker/kind/PostgreSQL/Redis) | 无 |
| 5 | FastAPI 骨架 | 1 |
| 6-9 | 可观测栈 (Prometheus/Grafana/Alertmanager/Loki) | 2 |
| 10-13 | Java/Spring Boot 样例服务 + K8S 部署 | 2 |
| 14-17 | Agent 核心 (LangGraph/Supervisor/DB/LLM) | 5 |
| 18-19 | Alert Agent (Webhook/去重/Incident创建) | 14,15,16 |
| 20-22 | 飞书通道 (SDK/卡片模板/告警推送) | 5 |
| 23-26 | Context Agent 工具集 (Prometheus/Loki/K8S/CMDB) | 6-9 |
| 27 | RCA Agent (CPU诊断+证据链+飞书推送) | 19,22,23-26 |
| 28 | Incident API | 16 |
| 29 | E2E 联调 | 全部 |

**任务间有依赖关系**：基础设施 → 可观测栈/样例服务 → Agent 框架 → Alert Agent → 工具集 → RCA Agent → API → 联调。飞书集成可与工具集并行开发。
