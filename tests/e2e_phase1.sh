#!/bin/bash
# Phase 1 端到端测试：告警接入 → 诊断 → 结果验证
# 测试流程：注入 CPU 故障 → 等待 Prometheus 告警 → Agent 自动创建工单 → 根因分析
set -e

echo "=== Phase 1 E2E Test ==="

# 获取目标 Pod 名称（order-service 的第一个 Pod）
TARGET_POD=$(kubectl get pods -n demo --context kind-ops-agent \
    -l app=order-service -o jsonpath='{.items[0].metadata.name}')

# 记录测试前的工单总数，用于后续判断是否有新工单
INCIDENTS_BEFORE=$(curl -s http://localhost:8000/api/v1/incidents | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d['total'])")

# 测试结束时自动关闭故障注入，避免持续影响集群
cleanup() {
    kubectl exec -n demo "$TARGET_POD" --context kind-ops-agent -- \
        curl -s -X POST http://localhost:8081/fault/reset >/dev/null || true
}
trap cleanup EXIT

# 第1步：注入 CPU 故障（死循环拉高 CPU）
echo "[1/5] Triggering CPU fault..."
kubectl exec -n demo "$TARGET_POD" --context kind-ops-agent -- \
    curl -s -X POST http://localhost:8081/fault/cpu?enable=true

# 第2步：等待 Prometheus 触发告警 → Alertmanager → Agent 创建工单（最长等 180 秒）
echo "[2/5] Waiting for Prometheus alert and Agent incident (up to 180s)..."
INCIDENTS=$INCIDENTS_BEFORE
for ((elapsed = 0; elapsed < 180; elapsed += 5)); do
    INCIDENTS=$(curl -s http://localhost:8000/api/v1/incidents | \
        python3 -c "import sys,json; d=json.load(sys.stdin); print(d['total'])")
    if [ "$INCIDENTS" -gt "$INCIDENTS_BEFORE" ]; then
        break
    fi
    sleep 5
done

# 第3步：确认 Prometheus 侧有活跃告警
echo "[3/5] Checking Prometheus alerts..."
ALERTS=$(curl -s "http://localhost:9090/api/v1/alerts" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data']['alerts']))")
echo "Active alerts: $ALERTS"

# 第4步：确认 Agent 侧新工单已创建
echo "[4/5] Checking Agent incidents..."
echo "Incidents created: $INCIDENTS"
if [ "$INCIDENTS" -gt "$INCIDENTS_BEFORE" ]; then
    echo "PASS: Incident created successfully"
else
    echo "FAIL: No new incident created"
    exit 1
fi

# 第5步：等待根因分析结果（最长等 30 秒），验证 root_cause 和 confidence 字段
echo "[5/5] Waiting for diagnosis result (up to 30s)..."
DIAGNOSIS=""
for ((elapsed = 0; elapsed < 30; elapsed += 2)); do
    DIAGNOSIS=$(curl -s http://localhost:8000/api/v1/incidents | python3 -c "
import sys,json
d=json.load(sys.stdin)
i = d['incidents'][0]
root_cause = i['root_cause']
confidence = i['confidence']
print('' if root_cause is None or confidence is None else f'root_cause={root_cause}, confidence={confidence}')
")
    if [ -n "$DIAGNOSIS" ]; then
        break
    fi
    sleep 2
done

if [ -z "$DIAGNOSIS" ]; then
    echo "FAIL: Diagnosis result was not completed"
    exit 1
fi
echo "$DIAGNOSIS"

echo "=== E2E Test Complete ==="
