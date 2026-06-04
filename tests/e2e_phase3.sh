#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
SERVICE="${SERVICE:-order-service}"
FINGERPRINT="phase3-$(date +%s)-$RANDOM"

echo "== Phase 3 E2E: Approval -> Execute -> Verify -> Report =="
echo "Agent API: ${BASE_URL}"

before_latest_id="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"

echo "[1/6] 触发 HighCPUUsage 告警"
curl -fsS -X POST "${BASE_URL}/api/v1/alerts" \
  -H "Content-Type: application/json" \
  -d "{
    \"receiver\": \"phase3-e2e\",
    \"alerts\": [{
      \"status\": \"firing\",
      \"labels\": {
        \"alertname\": \"HighCPUUsage\",
        \"service\": \"${SERVICE}\",
        \"env\": \"prod\",
        \"severity\": \"P1\"
      },
      \"annotations\": {
        \"summary\": \"Phase 3 E2E CPU alert\",
        \"value\": \"CPU > 90%\"
      },
      \"startsAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"fingerprint\": \"${FINGERPRINT}\"
    }]
  }" >/dev/null

incident_id=""
echo "[2/6] 等待诊断和待审批状态"
for _ in $(seq 1 60); do
  incident_json="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1")"
  latest_id="$(printf '%s' "${incident_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"
  if [ -n "${latest_id}" ] && [ "${latest_id}" != "${before_latest_id}" ]; then
    detail_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${latest_id}")"
    approval_status="$(printf '%s' "${detail_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("approval_status") or "")')"
    if [ "${approval_status}" = "pending" ]; then
      incident_id="${latest_id}"
      break
    fi
  fi
  sleep 2
done

if [ -z "${incident_id}" ]; then
  echo "未在规定时间内看到 pending 审批事件"
  exit 1
fi
echo "Incident: ${incident_id}"

echo "[3/6] 模拟飞书批准执行"
curl -fsS -X POST "${BASE_URL}/api/v1/approvals/callback" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"card_action\",\"operator\":{\"name\":\"phase3-e2e\"},\"action\":{\"value\":{\"action\":\"approve\",\"incident_id\":\"${incident_id}\"}}}" >/dev/null

echo "[4/6] 等待执行记录"
execution_status=""
for _ in $(seq 1 60); do
  executions_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/executions")"
  execution_status="$(printf '%s' "${executions_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); executions=data.get("executions") or []; print(executions[0].get("status", "") if executions else "")')"
  if [ -n "${execution_status}" ]; then
    break
  fi
  sleep 2
done

if [ "${execution_status}" != "success" ]; then
  echo "执行记录不是 success: ${execution_status:-<empty>}"
  exit 1
fi
echo "Execution status: ${execution_status}"

echo "[5/6] 等待故障报告"
for _ in $(seq 1 90); do
  if curl -fsS "${BASE_URL}/api/v1/reports/${incident_id}" >/tmp/phase3-report.json 2>/dev/null; then
    break
  fi
  sleep 2
done

python3 - <<'PY'
import json
from pathlib import Path

path = Path("/tmp/phase3-report.json")
if not path.exists():
    raise SystemExit("报告文件不存在")
data = json.loads(path.read_text())
content = data.get("content") or ""
assert "## 执行结果" in content, content
assert "## 验证结果" in content, content
print("Report sections verified")
PY

echo "[6/6] 校验 Web Console 页面"
curl -fsS "${BASE_URL}/executions.html" >/dev/null
curl -fsS "${BASE_URL}/reports.html" >/dev/null

echo "Phase 3 E2E passed: ${incident_id}"
