# CPU 高负载处置 Runbook

## 触发条件

- CPU 使用率 > 90% 持续 1 分钟以上

## 处置步骤

1. [低风险] 查看 Grafana Dashboard 确认 CPU 趋势和 QPS 变化
2. [低风险] 检查 Pod 状态: `kubectl get pods -n demo -l app={{service}}`
3. [中风险] 如果所有 Pod CPU 均高且 QPS 上涨，扩容服务: `kubectl scale deployment {{service}} -n demo --replicas={{replicas}}`
4. [中风险] 如果仅个别 Pod CPU 高，重启异常 Pod: `kubectl delete pod {{pod_name}} -n demo`
5. [高风险] 如果扩容后仍未缓解，检查依赖服务状态和数据库连接池
6. [低风险] 故障恢复后，记录时间线和根因到故障库

## 回滚方案

- 缩容回原始副本数: `kubectl scale deployment {{service}} -n demo --replicas={{original_replicas}}`

## 预计恢复时间

- 扩容方案: 2 分钟
- 重启 Pod: 1 分钟
