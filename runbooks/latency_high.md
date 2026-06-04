# 延迟升高处置 Runbook

## 触发条件

- P99 响应时间 > 1s 持续 1 分钟以上

## 处置步骤

1. [低风险] 查看 Grafana RT 趋势和 QPS 关联
2. [低风险] 检查慢查询和 DB 连接池使用率
3. [中风险] 如果是 DB 慢查询，优化索引或限流
4. [中风险] 如果是下游服务响应慢，添加超时和熔断
5. [低风险] 如果是流量突增，扩容并进行缓存预热: `kubectl scale deployment {{service}} -n demo --replicas={{replicas}}`

## 回滚方案

- 恢复扩容: `kubectl scale deployment {{service}} -n demo --replicas={{original_replicas}}`

## 预计恢复时间

- 扩容: 2 分钟
- DB 优化: 视情况而定
