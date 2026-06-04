# Error Rate 异常处置 Runbook

## 触发条件

- 5xx 错误率 > 5% 持续 1 分钟以上

## 处置步骤

1. [低风险] 查看 Loki 错误日志，定位异常堆栈: `{app="{{service}}"} |= "ERROR"`
2. [低风险] 检查依赖服务健康状态，包括 payment-service、inventory-service、postgres 和 redis
3. [中风险] 如果依赖服务异常，等待依赖恢复或切换降级开关
4. [中风险] 如果是代码 bug，回滚到上一版本: `kubectl rollout undo deployment {{service}} -n demo`
5. [高风险] 如果数据库超时，检查慢查询并扩容连接池
6. [低风险] 确认恢复后，关闭故障注入: `kubectl exec {{pod_name}} -n demo -- curl -s -X POST http://localhost:8081/fault/reset`

## 回滚方案

- 回滚部署: `kubectl rollout undo deployment {{service}} -n demo`

## 预计恢复时间

- 回滚: 2 分钟
- 依赖恢复: 视依赖方情况而定
