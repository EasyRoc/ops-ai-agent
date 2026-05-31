# 运维 Agent 开发任务清单

基于 [架构分析](../关于运维%20Agent%20的架构分析.md)、[PRD](../运维%20Agent%20产品需求文档（PRD）.md)、[技术实现方案](../运维%20Agent%20技术实现方案.md) 拆分。

---

## Phase 1：只读诊断（2周）

**目标**：Agent 能接收告警、自动查询可观测数据、生成诊断结论，不执行任何变更。

### 1.1 基础设施搭建

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 1.1.1 | Docker 开发环境初始化 | `docker-compose.yml`，包含 PostgreSQL + Redis 容器 | 一键启动开发环境 |
| 1.1.2 | kind 集群创建 | 创建本地 Kubernetes 集群（kind），用于部署样例服务和可观测栈 | `kind-config.yaml` |
| 1.1.3 | PostgreSQL 表结构 | 创建 `incidents`、`executions`、`reports` 三张表，含索引 | `migrations/001_init.sql` |
| 1.1.4 | Redis 配置 | 配置 Redis 用于告警去重缓存和会话状态 | 连接配置 |
| 1.1.5 | 项目骨架 | FastAPI 项目结构、配置管理（`.env`）、日志配置 | `main.py`、`config.py` |

### 1.2 可观测栈部署

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 1.2.1 | Prometheus 部署 | Helm 安装 Prometheus + 配置 ServiceMonitor 抓取样例服务指标 | Prometheus 正常运行 |
| 1.2.2 | Grafana 部署 | Helm 安装 Grafana + 导入基础 Dashboard（CPU/Mem/QPS/RT） | Grafana Dashboard |
| 1.2.3 | Alertmanager 配置 | 配置告警规则（CPU>90%、Error Rate>5%）+ Webhook 指向 Agent API | 告警→Agent 链路通 |
| 1.2.4 | Loki + Promtail 部署 | 日志采集管道，Promtail 采集 Pod 日志→Loki 存储 | 可查询 Pod 日志 |

### 1.3 样例业务系统（Java/Spring Boot）

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 1.3.1 | Spring Boot 多模块项目 | 4 个 Spring Boot 服务：frontend、order、payment、inventory，含 `/health`（Actuator）和 `/actuator/prometheus`（Micrometer） | K8S Deployment + Service |
| 1.3.2 | 故障注入接口 | 每个服务暴露 `/fault/cpu`（死循环）、`/fault/memory`（堆内存填充）、`/fault/error`（返回500）、`/fault/latency`（sleep 5s） | 可手动触发故障 |
| 1.3.3 | Dockerfile + Jib 构建 | 多阶段 Docker 构建（OpenJDK 17），或 Jib Maven Plugin 构建镜像 | 镜像可 build 并 push 到 kind |
| 1.3.4 | 样例业务部署 | 所有服务部署到 kind 集群，确认 Prometheus 能抓取 Actuator metrics | 完整运行环境 |

### 1.4 Agent 核心框架

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 1.4.1 | LangGraph Workflow 定义 | 定义告警处理的状态图和节点：Alert→Context→RCA→(End) | `workflows/alert_workflow.py` |
| 1.4.2 | Supervisor Agent | 任务编排器，管理 workflow 状态流转和子 Agent 调度 | `agents/supervisor.py` |
| 1.4.3 | LLM 适配层 | OpenAI Compatible API 封装，支持本地 Ollama 模型（Qwen3/DeepSeek） | `llm/client.py` |
| 1.4.4 | 数据库访问层 | SQLAlchemy ORM 模型 + CRUD 操作封装 | `db/models.py`、`db/crud.py` |

### 1.5 Alert Agent

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 1.5.1 | Webhook API | `POST /api/v1/alerts` 接收 Alertmanager webhook，解析 JSON payload | `api/v1/alerts.py` |
| 1.5.2 | 告警去重与聚合 | Redis 缓存 5 分钟内相同告警不重复创建 Incident；同服务多告警聚合 | `agents/alert.py` |
| 1.5.3 | Incident 创建 | 解析告警→提取 service/env/severity→写入 `incidents` 表→返回 incident_id | Incident 可查询 |
| 1.5.4 | 飞书 Bot 基础接入 | 创建飞书应用 + 配置 Bot 权限（im:message、im:message:send_as_bot），Webhook 地址配置 | 飞书 Bot 可收发消息 |

### 1.6 飞书通知通道

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 1.6.1 | 飞书消息卡片模板 | 告警通知卡片模板（标题+服务+级别+当前值+时间），诊断结果卡片模板（根因+证据+置信度） | `templates/cards/` |
| 1.6.2 | 飞书 SDK 封装 | 封装飞书 Open API：发送消息、发送卡片、更新卡片内容、接收回调 | `channels/feishu.py` |
| 1.6.3 | 告警→飞书推送 | Alert Agent 创建 Incident 后自动推送告警卡片到指定飞书群 | 告警即时通知 |

### 1.7 Context Agent

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 1.7.1 | Prometheus 查询工具 | 封装 PromQL：CPU/Mem/QPS/RT/ErrorRate 查询函数 | `tools/prometheus.py` |
| 1.7.2 | Loki 日志查询工具 | 封装 LogQL：按 service + keyword + 时间范围查日志 | `tools/loki.py` |
| 1.7.3 | Kubernetes 查询工具 | kubectl/API 封装：get_pods、describe_pod、get_events、get_deployments | `tools/kubernetes.py` |
| 1.7.4 | CMDB 查询工具（Mock） | Mock CMDB：get_service_owner、get_dependencies、get_oncall | `tools/cmdb.py` |

### 1.8 RCA Agent

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 1.8.1 | CPU 高诊断规则 | 诊断决策树：流量上涨？单 Pod 异常？最近发布？节点异常？ | `agents/rca.py` |
| 1.8.2 | 证据链构造 | 收集的所有指标/日志/事件→结构化 evidence 数组→附在诊断结论后 | 结构化输出 |
| 1.8.3 | 置信度计算 | 根据证据完备度计算 confidence score（0~1），证据越多置信度越高 | confidence 字段 |

### 1.9 交互与联调

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 1.9.1 | Incident API | `GET /api/v1/incidents/{id}` 查询诊断状态和结论，`GET /api/v1/incidents` 列表 | RESTful API |
| 1.9.2 | 端到端联调 | 故障注入→Prometheus 告警→Alertmanager→Agent→诊断→飞书卡片推送 | 全链路跑通 |
| 1.9.3 | 诊断结果格式化 | 将 Agent 输出结构化为飞书卡片 JSON（告警信息+根因+证据+建议+操作按钮） | 标准化飞书卡片格式 |

---

## Phase 2：方案生成 + 人工确认（2周）

**目标**：匹配 Runbook 生成处置建议、风险评估、人工审批流。Agent 能出方案但不自动执行。

### 2.1 OOM & Error Rate 诊断

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 2.1.1 | OOM 诊断规则 | 判断：内存泄漏？limit 过小？流量突增？→ 匹配处置建议 | `agents/rca.py` 扩展 |
| 2.1.2 | Error Rate 诊断规则 | 判断：代码异常？依赖服务异常？DB 超时？→ 关联日志和链路 | `agents/rca.py` 扩展 |
| 2.1.3 | 部署变更关联 | 查询最近部署记录（Git/ArgoCD mock），关联告警时间和部署时间 | 部署关联分析 |

### 2.2 Runbook Agent

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 2.2.1 | Runbook 模板定义 | 创建 `runbooks/cpu_high.md`、`oom.md`、`error_rate.md`、`disk_full.md` | Markdown Runbook |
| 2.2.2 | Runbook 匹配引擎 | 根据 RCA 结论匹配对应 Runbook，提取处置步骤 | `agents/runbook.py` |
| 2.2.3 | 处置方案生成 | 将 Runbook 步骤 + 当前上下文（service/env/current metrics）组合为可执行方案 | 结构化处置方案 |

### 2.3 Risk Agent

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 2.3.1 | 风险分级逻辑 | 根据动作类型、影响范围、环境分级：低/中/高/极高 | `agents/risk.py` |
| 2.3.2 | 动作白名单 | 定义允许执行的动作清单（restart_pod、scale_deployment、clean_logs），其他一律拒绝 | 白名单配置 |

### 2.4 审批流（飞书交互）

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 2.4.1 | 审批 API | `POST /api/v1/approvals/{incident_id}` — 批准/拒绝/转人工 | `api/v1/approvals.py` |
| 2.4.2 | 审批状态机 | pending → approved/rejected/escalated，与 Incident 状态联动 | 状态流转 |
| 2.4.3 | 飞书卡片交互审批 | 诊断结果卡片内嵌 [批准执行] [拒绝] [转人工] 按钮，飞书回调→Agent 处理审批 | `channels/feishu.py` 扩展 |
| 2.4.4 | 飞书执行进度推送 | 执行过程中飞书卡片实时更新：权限校验→扩缩容→Pod状态→验证结果 | 卡片内容动态更新 |

### 2.5 Web Console（基础版）

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 2.5.1 | Incident 列表页 | 展示所有 Incident，状态筛选（diagnosing/pending_approval/resolved） | 列表页 |
| 2.5.2 | Incident 详情页 | 展示单个 Incident 的诊断过程、证据链、处置方案、审批按钮 | 详情页 |
| 2.5.3 | 仪表盘首页 | 概览：今日告警数、处理中、已恢复、平均诊断时间 | Dashboard |

---

## Phase 3：自动执行 + 验证 + 沉淀（2周）

**目标**：白名单内的低风险动作自动执行、事后验证、Incident Report 自动生成。

### 3.1 Executor Agent

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 3.1.1 | K8S 执行器 | 封装安全执行接口：scale_deployment、restart_pod（仅白名单动作） | `agents/executor.py` |
| 3.1.2 | GitOps 执行器（Mock） | 模拟修改配置仓库 + 创建 PR 的流程 | `agents/executor.py` |
| 3.1.3 | 执行状态追踪 | 执行过程实时更新 `executions` 表，记录每步结果 | 执行可追踪 |

### 3.2 Verify Agent

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 3.2.1 | 恢复验证逻辑 | 执行后轮询指标：CPU<阈值? RT恢复? ErrorRate正常? Pod全部Ready? | `agents/verify.py` |
| 3.2.2 | 验证超时与降级 | 验证超时（如5分钟未恢复）→ 升级人工 | 超时处理 |

### 3.3 Report Agent

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 3.3.1 | Incident Report 生成 | 自动汇总：时间线 + 根因 + 处置 + 结果 + 后续建议 → 写入 `reports` 表 | `agents/report.py` |
| 3.3.2 | Report API | `GET /api/v1/reports/{incident_id}` 返回 Markdown/JSON 格式报告 | Report 可查询 |

### 3.4 审计与安全

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 3.4.1 | 审计日志 | 所有 Agent 动作（查询/执行/审批）写入审计表，含 who/when/what/result | `audit_logs` 表 |
| 3.4.2 | RBAC 基础实现 | 三种角色：viewer（查看）、operator（审批+执行）、admin（全部） | `middleware/auth.py` |

### 3.5 知识沉淀

| # | 任务 | 描述 | 产出 |
|---|------|------|------|
| 3.5.1 | 历史故障库 | 已解决的 Incident→结构化为故障案例（根因+症状+处置+效果） | `fault_cases` 表 |
| 3.5.2 | Runbook 优化建议 | 根据实际执行效果标注 Runbook 有效性，标记需改进的步骤 | Runbook 评分 |

---

## 总览

| Phase | 周期 | 任务数 | 核心交付 |
|-------|------|--------|----------|
| Phase 1 | 2 周 | 29 | 只读诊断：告警→诊断→飞书通知 |
| Phase 2 | 2 周 | 15 | 方案+审批：Runbook 匹配→风险评估→飞书卡片审批 |
| Phase 3 | 2 周 | 9 | 自动闭环：执行→验证→报告→沉淀 |
| **合计** | **6 周** | **53** | 完整运维 Agent 系统 |

## 技术栈参考

| 层次 | 组件 |
|------|------|
| Agent 框架 | FastAPI + LangGraph + OpenAI Compatible LLM (Ollama: Qwen3/DeepSeek) |
| 样例业务 | Java 17 + Spring Boot 3 + Micrometer + Actuator |
| 数据层 | PostgreSQL + Redis |
| 可观测 | Prometheus + Grafana + Alertmanager + Loki + Promtail |
| 消息通道 | 飞书 Open API（Bot 消息 + 卡片消息 + 交互回调） |
| 基础设施 | Docker + kind (Kubernetes) + Helm |
| 管理后台 | 简单 HTML/JS（内嵌于 FastAPI static） |
