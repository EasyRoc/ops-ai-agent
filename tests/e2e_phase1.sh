#!/bin/bash
set -e

echo "=== Phase 1 E2E Test ==="

TARGET_POD=$(kubectl get pods -n demo --context kind-ops-agent \
    -l app=order-service -o jsonpath='{.items[0].metadata.name}')
INCIDENTS_BEFORE=$(curl -s http://localhost:8000/api/v1/incidents | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d['total'])")

cleanup() {
    kubectl exec -n demo "$TARGET_POD" --context kind-ops-agent -- \
        curl -s -X POST http://localhost:8081/fault/reset >/dev/null || true
}
trap cleanup EXIT

echo "[1/5] Triggering CPU fault..."
kubectl exec -n demo "$TARGET_POD" --context kind-ops-agent -- \
    curl -s -X POST http://localhost:8081/fault/cpu?enable=true

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

echo "[3/5] Checking Prometheus alerts..."
ALERTS=$(curl -s "http://localhost:9090/api/v1/alerts" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data']['alerts']))")
echo "Active alerts: $ALERTS"

echo "[4/5] Checking Agent incidents..."
echo "Incidents created: $INCIDENTS"
if [ "$INCIDENTS" -gt "$INCIDENTS_BEFORE" ]; then
    echo "PASS: Incident created successfully"
else
    echo "FAIL: No new incident created"
    exit 1
fi

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
