#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
SERVICE="${SERVICE:-order-service}"
FINGERPRINT="phase2-$(date +%s)-$RANDOM"

echo "== Phase 2 E2E: Runbook + Approval =="
echo "Agent API: ${BASE_URL}"

before_latest_id="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"

curl -fsS -X POST "${BASE_URL}/api/v1/alerts" \
  -H "Content-Type: application/json" \
  -d "{
    \"receiver\": \"phase2-e2e\",
    \"alerts\": [{
      \"status\": \"firing\",
      \"labels\": {
        \"alertname\": \"HighCPUUsage\",
        \"service\": \"${SERVICE}\",
        \"env\": \"prod\",
        \"severity\": \"P1\"
      },
      \"annotations\": {
        \"summary\": \"Phase 2 E2E CPU alert\",
        \"value\": \"CPU > 90%\"
      },
      \"startsAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"fingerprint\": \"${FINGERPRINT}\"
    }]
  }" >/dev/null

incident_id=""
for _ in $(seq 1 60); do
  incident_json="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1")"
  latest_id="$(printf '%s' "${incident_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"
  if [ -n "${latest_id}" ] && [ "${latest_id}" != "${before_latest_id}" ]; then
    incident_id="${latest_id}"
    break
  fi
  sleep 2
done

if [ -z "${incident_id}" ]; then
  echo "未在规定时间内看到新事件"
  exit 1
fi

echo "新事件: ${incident_id}"

for _ in $(seq 1 60); do
  detail_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}")"
  approval_status="$(printf '%s' "${detail_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("approval_status") or "")')"
  if [ "${approval_status}" = "pending" ]; then
    break
  fi
  sleep 2
done

DETAIL_JSON="${detail_json}" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["DETAIL_JSON"])
assert data.get("runbook_name") == "cpu_high.md", data
assert data.get("action_plan"), data
assert data.get("risk_assessment", {}).get("level"), data
assert data.get("approval_status") == "pending", data
print("Runbook / risk / pending approval verified")
PY

curl -fsS -X POST "${BASE_URL}/api/v1/approvals/callback" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"card_action\",\"action\":{\"value\":{\"action\":\"approve\",\"incident_id\":\"${incident_id}\"}}}" >/dev/null

for _ in $(seq 1 20); do
  approval_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/approval")"
  status="$(printf '%s' "${approval_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("approval_status") or "")')"
  if [ "${status}" = "approved" ]; then
    break
  fi
  sleep 1
done

if [ "${status}" != "approved" ]; then
  echo "审批状态未更新为 approved"
  exit 1
fi

curl -fsS "${BASE_URL}/" >/dev/null
curl -fsS "${BASE_URL}/incident-detail.html?id=${incident_id}" >/dev/null

echo "Phase 2 E2E passed: ${incident_id}"
