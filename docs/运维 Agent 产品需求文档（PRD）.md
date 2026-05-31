# 运维 Agent 产品需求文档（PRD）

# 项目背景

随着企业微服务化、云原生化的发展，运维体系面临如下问题：

### 1\.1 告警风暴

典型情况：

```Plain Text
Redis异常
↓
订单服务超时
↓
库存服务异常
↓
支付服务异常
↓
产生上百条告警
```

运维需要：

```Plain Text
登录 Grafana
登录 Loki
登录 K8S
登录 ArgoCD
登录 CMDB
```

逐个排查。

故障定位耗时长。

---

### 1\.2 故障处理依赖经验

例如：

```Plain Text
CPU高怎么办？
OOM怎么办？
Kafka积压怎么办？
```

通常依赖：

```Plain Text
资深SRE
资深开发
```

新人处理效率低。

---

### 1\.3 重复劳动严重

大量工作属于：

```Plain Text
查指标
查日志
查Pod
查发布记录
写故障报告
```

重复率高。

---

# 产品目标

建设一套本地可部署的运维 Agent 系统，实现：

### 自动感知

能够自动接收：

```Plain Text
Prometheus告警
K8S Event
用户询问
定时巡检
```

---

### 自动诊断

自动收集：

```Plain Text
Metrics
Logs
Traces
Events
CMDB
Deployment
```

生成：

```Plain Text
故障结论
根因分析
证据链
```

---

### 自动决策

自动输出：

```Plain Text
处置建议
风险等级
执行计划
```

---

### 自动执行

支持：

```Plain Text
扩容
重启Pod
日志清理
证书续期
```

---

### 自动验证

验证：

```Plain Text
CPU恢复
RT恢复
错误率恢复
Pod Ready
```

---

### 自动沉淀

自动生成：

```Plain Text
Incident Report
Runbook
知识库
```

---

# 产品范围

---

## 3\.1 一期目标

定位：

```Plain Text
智能运维 Copilot
```

能力：

```Plain Text
接收告警
自动诊断
自动生成方案
人工确认
执行修复
自动验证
生成报告
```

---

## 3\.2 不在一期范围

不支持：

```Plain Text
数据库DDL执行
主从切换
网络变更
权限变更
安全处置
```

---

# 用户角色

---

## 运维工程师

权限：

```Plain Text
查看诊断
批准执行
查看报告
```

---

## SRE

权限：

```Plain Text
执行动作
审批动作
管理Runbook
```

---

## 管理员

权限：

```Plain Text
全部权限
```

---

# 核心业务流程

## 流程1：告警处理

```Plain Text
Prometheus
↓
Alertmanager
↓
Webhook
↓
Agent

Agent自动诊断
↓
Agent输出方案
↓
人工确认
↓
Agent执行
↓
Agent验证
↓
关闭Incident
```

---

## 流程2：用户主动询问

例如：

```Plain Text
为什么order-service异常？
```

Agent：

```Plain Text
查询指标
↓
查询日志
↓
查询Pod
↓
查询发布记录
↓
输出RCA
```

---

# 功能需求

---

## FR\-01 告警接收

支持：

```Plain Text
Prometheus
Alertmanager
Webhook
```

能力：

```Plain Text
告警解析
告警聚合
告警去重
创建Incident
```

---

## FR\-02 上下文收集

采集：

### Metrics

```Plain Text
CPU
Memory
QPS
RT
Error Rate
```

### Logs

```Plain Text
异常日志
慢日志
OOM日志
```

### Kubernetes

```Plain Text
Pod
Deployment
Event
Node
```

### 发布记录

```Plain Text
Git
Jenkins
ArgoCD
```

---

## FR\-03 RCA

输出：

```JSON
{
  "root_cause": "",
  "confidence": 0.85,
  "evidence": []
}
```

---

## FR\-04 Runbook匹配

支持：

```Plain Text
CPU高
OOM
Error Rate高
Disk Full
```

自动匹配：

```Plain Text
Runbook
SOP
```

---

## FR\-05 风险评估

输出：

```Plain Text
低风险
中风险
高风险
```

---

## FR\-06 审批

支持：

```Plain Text
Web UI
飞书
企业微信
```

审批动作：

```Plain Text
批准
拒绝
转人工
```

---

## FR\-07 执行

允许：

```Plain Text
restart_pod
scale_deployment
clean_logs
```

禁止：

```Plain Text
delete_namespace
drop_database
```

---

## FR\-08 验证

验证：

```Plain Text
CPU恢复
RT恢复
Error恢复
```

---

## FR\-09 报告

自动生成：

```Plain Text
事件时间线
根因
执行动作
结果
建议
```

---

# 非功能需求

---

## 性能

```Plain Text
诊断时间 < 60秒
```

---

## 可用性

```Plain Text
99.9%
```

---

## 安全

```Plain Text
RBAC
审批
审计日志
```

---

# MVP场景

一期支持：

```Plain Text
CPU高
OOM
Error Rate高
```

---

---

# 

