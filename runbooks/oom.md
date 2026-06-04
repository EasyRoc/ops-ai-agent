# OOM 内存溢出处置 Runbook

## 触发条件

- 内存使用率 > 90% 或 Pod 因 OOMKilled 重启

## 处置步骤

1. [低风险] 查看 Grafana 内存趋势，确认是缓慢增长还是突增
2. [低风险] 检查 Pod 重启次数: `kubectl get pods -n demo -l app={{service}}`
3. [中风险] 如果是内存泄漏，重启 Pod 临时释放: `kubectl delete pod {{pod_name}} -n demo`
4. [中风险] 如果是 limit 过小，调大 memory limit: `kubectl set resources deployment {{service}} -n demo --limits=memory={{new_limit}}`
5. [高风险] 如果是流量突增，扩容并启用限流
6. [低风险] 抓取 heap dump 供开发分析: `kubectl exec {{pod_name}} -n demo -- jcmd 1 GC.heap_dump /tmp/heap.hprof`

## 回滚方案

- 恢复原始 memory limit: `kubectl set resources deployment {{service}} -n demo --limits=memory={{original_limit}}`

## 预计恢复时间

- 重启 Pod: 1 分钟
- 调整 limit: 3 分钟，需滚动更新
