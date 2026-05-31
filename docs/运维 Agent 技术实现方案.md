# 运维 Agent 技术实现方案

# 总体架构

```Plain Text
┌──────────────┐
                 │ Alertmanager │
                 └──────┬───────┘
                        │
                        ▼
                ┌──────────────┐
                │ Ops Agent API│
                └──────┬───────┘
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
  LangGraph Workflow         PostgreSQL
          │
          ▼
  Multi-Agent Layer
          │
 ┌────────┼────────┐
 ▼        ▼        ▼
RCA   Runbook   Executor
Agent  Agent     Agent
          │
          ▼
  K8S / Prometheus / Loki
```

---

# 技术栈

## 基础设施

```YAML
Docker
kind
Helm
```

---

## Agent

```YAML
FastAPI
LangGraph
OpenAI Compatible LLM
```

推荐：

```YAML
Qwen3
DeepSeek-R1
Llama3
```

本地部署：

[Ollama](https://ollama.com/?utm_source=chatgpt.com)

---

## 数据层

```YAML
PostgreSQL
Redis
```

---

## 监控

```YAML
Prometheus
Grafana
Alertmanager
```

---

## 日志

```YAML
Loki
Promtail
```

---

## 链路

```YAML
Jaeger
```

二期接入。

---

# 多Agent设计

---

## Supervisor Agent

职责：

```Plain Text
任务编排
状态管理
Agent调度
```

---

## Alert Agent

输入：

```JSON
{
  "alert":"HighCPU"
}
```

输出：

```JSON
{
  "incident":"INC-001"
}
```

---

## Context Agent

负责：

```Plain Text
Prometheus查询
Loki查询
K8S查询
```

---

## RCA Agent

负责：

```Plain Text
根因分析
```

输出：

```JSON
{
  "root_cause":"Traffic Surge"
}
```

---

## Runbook Agent

负责：

```Plain Text
匹配CPU高Runbook
匹配OOM Runbook
```

---

## Risk Agent

负责：

```Plain Text
风险评估
审批判断
```

---

## Executor Agent

负责：

```Plain Text
扩容
重启Pod
```

---

## Verify Agent

负责：

```Plain Text
恢复验证
```

---

## Report Agent

负责：

```Plain Text
生成Incident Report
```

---

# Workflow设计

## CPU高流程

```Plain Text
告警
↓
Alert Agent
↓
Context Agent
↓
RCA Agent
↓
Runbook Agent
↓
Risk Agent
↓
人工确认
↓
Executor Agent
↓
Verify Agent
↓
Report Agent
```

---

# 样例业务系统

部署：

```Plain Text
frontend-service
order-service
payment-service
inventory-service
postgres
redis
```

---

# 故障注入设计

## CPU

接口：

```HTTP
POST /fault/cpu
```

行为：

```Python
while True:
    pass
```

---

## OOM

```HTTP
POST /fault/memory
```

行为：

```Python
arr.append("x"*1024*1024)
```

---

## Error

```HTTP
POST /fault/error
```

行为：

```Python
return 500
```

---

## Latency

```HTTP
POST /fault/latency
```

行为：

```Python
sleep(5)
```

---

# 数据存储设计

## incidents

```SQL
id
service
status
root_cause
severity
created_at
```

---

## executions

```SQL
id
incident_id
action
operator
result
```

---

## reports

```SQL
id
incident_id
content
```

---

# Runbook设计

目录：

```Plain Text
runbooks/

cpu_high.md
oom.md
error_rate.md
disk_full.md
```

示例：

```Markdown
# CPU High

判断：
QPS是否上涨

如果上涨：
扩容

如果未上涨：
检查线程栈
```

---

# 安全设计

白名单动作：

```Plain Text
restart_pod
scale_deployment
get_logs
describe_pod
```

禁止：

```Plain Text
shell
kubectl delete
drop database
```

---

# 开发路线图

## Phase 1

能力：

```Plain Text
Prometheus接入
Loki接入
K8S接入
CPU高诊断
```

周期：

```Plain Text
2周
```

---

## Phase 2

能力：

```Plain Text
OOM
Error Rate
Runbook
审批
```

周期：

```Plain Text
2周
```

---

## Phase 3

能力：

```Plain Text
自动执行
自动验证
报告生成
```

周期：

```Plain Text
2周
```

---

# 最终目标架构

```Plain Text
Alertmanager
                           │
                           ▼
                    Supervisor Agent
                           │
      ┌────────────────────┼────────────────────┐
      ▼                    ▼                    ▼
 Alert Agent        Context Agent        RCA Agent
                                                │
                                                ▼
                                        Runbook Agent
                                                │
                                                ▼
                                          Risk Agent
                                                │
                                         人工审批
                                                │
                                                ▼
                                        Executor Agent
                                                │
                                                ▼
                                         Verify Agent
                                                │
                                                ▼
                                         Report Agent

                    ─────────────────────────

 Prometheus | Loki | Kubernetes | PostgreSQL

                    ─────────────────────────

        Demo故障场景：
        CPU高
        OOM
        Error Rate高
        Latency高
```

这个方案已经可以支撑一个完整的、可本地运行的、具备企业级雏形的运维 Agent Demo，并且后续可以逐步演进到真正的 AIOps 平台。

