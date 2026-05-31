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
