#!/usr/bin/env bash
# E2E: AI 兜底 -> 批准执行 -> 执行记录/重试状态 -> 审计日志 -> Web Console
# 需要本地 Agent、PostgreSQL、Redis、Prometheus/K8S demo、LLM 配置可用。
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
SERVICE="${SERVICE:-order-service}"
ALERT_NAME="${E2E_ALERT_NAME:-ThreadPoolExhausted}"
FINGERPRINT="retry-loop-$(date +%s)-$RANDOM"

echo "== Retry Loop E2E: AI Fallback -> Execute -> Retry Observability =="
echo "Agent API: ${BASE_URL}"
echo "Alert: ${ALERT_NAME}, service: ${SERVICE}"

curl -fsS "${BASE_URL}/health" >/dev/null || {
  echo "FAIL: Agent 不可达，请先启动服务: ${BASE_URL}/health"
  exit 1
}

before_latest_id="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"

echo "[1/8] 发送未命中预置 Runbook 的告警"
curl -fsS -X POST "${BASE_URL}/api/v1/alerts" \
  -H "Content-Type: application/json" \
  -d "{
    \"receiver\": \"retry-loop-e2e\",
    \"alerts\": [{
      \"status\": \"firing\",
      \"labels\": {
        \"alertname\": \"${ALERT_NAME}\",
        \"service\": \"${SERVICE}\",
        \"env\": \"prod\",
        \"severity\": \"P2\"
      },
      \"annotations\": {
        \"summary\": \"Retry loop E2E test\",
        \"value\": \"thread pool exhausted, all threads blocked\"
      },
      \"startsAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"fingerprint\": \"${FINGERPRINT}\"
    }]
  }" >/dev/null

echo "[2/8] 等待 AI 兜底工单创建和方案生成"
incident_id=""
for _ in $(seq 1 90); do
  incident_json="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1")"
  latest_id="$(printf '%s' "${incident_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"
  if [ -n "${latest_id}" ] && [ "${latest_id}" != "${before_latest_id}" ]; then
    detail_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${latest_id}")"
    if DETAIL_JSON="${detail_json}" python3 - <<'PY'
import json
import os
import sys

data = json.loads(os.environ["DETAIL_JSON"])
risk = data.get("risk_assessment") or {}
has_ai = (
    data.get("runbook_name") == "ai_fallback"
    and risk.get("ai_generated") is True
    and bool(data.get("action_plan"))
    and data.get("approval_status") == "pending"
)
sys.exit(0 if has_ai else 1)
PY
    then
      incident_id="${latest_id}"
      break
    fi
  fi
  sleep 2
done

if [ -z "${incident_id}" ]; then
  echo "FAIL: AI 兜底工单未在规定时间内创建"
  exit 1
fi
echo "Incident: ${incident_id}"

echo "[3/8] 验证 AI 兜底字段"
DETAIL_JSON="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}")"
DETAIL_JSON="${DETAIL_JSON}" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["DETAIL_JSON"])
risk = data.get("risk_assessment") or {}
assert data.get("runbook_name") == "ai_fallback", data
assert risk.get("ai_generated") is True, data
assert data.get("action_plan"), data
print(f"AI fallback plan generated: {len(data['action_plan'])} steps, confidence={risk.get('ai_confidence', 'N/A')}")
PY

echo "[4/8] 模拟飞书批准 AI 自动执行"
curl -fsS -X POST "${BASE_URL}/api/v1/approvals/callback" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"card_action\",\"operator\":{\"name\":\"retry-loop-e2e\"},\"action\":{\"value\":{\"action\":\"approve_ai\",\"incident_id\":\"${incident_id}\"}}}" >/dev/null

echo "[5/8] 等待执行记录或重试状态更新"
for _ in $(seq 1 90); do
  executions_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/executions")"
  exec_count="$(printf '%s' "${executions_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(len(data.get("executions") or []))')"
  detail_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}")"
  retry_count="$(printf '%s' "${detail_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("retry_count") or 0)')"
  if [ "${exec_count}" -gt 0 ] || [ "${retry_count}" -gt 0 ]; then
    break
  fi
  sleep 2
done

echo "[6/8] 输出执行记录"
executions_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/executions")"
EXECUTIONS_JSON="${executions_json}" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["EXECUTIONS_JSON"])
executions = data.get("executions") or []
print(f"Execution records: {len(executions)}")
for item in executions:
    print(f"  - round={item.get('round', 1)} status={item.get('status')} action={(item.get('action') or '')[:70]}")
PY

echo "[7/8] 验证审计日志"
audit_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/audit?limit=50")"
AUDIT_JSON="${audit_json}" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["AUDIT_JSON"])
logs = data.get("audit_logs") or []
assert logs, "audit logs should not be empty"
actions = [item.get("action") for item in logs]
assert "ai_plan_generated" in actions, actions
print(f"Audit logs: {len(logs)}")
for item in logs:
    print(f"  [{item.get('actor')}] {item.get('action')} @ {item.get('created_at', '?')}")
PY

echo "[8/8] 验证 Web Console 时间线页面"
detail_page="$(curl -fsS "${BASE_URL}/incident-detail.html?id=${incident_id}")"
printf '%s' "${detail_page}" | grep -q "retryTimelineSection"
printf '%s' "${detail_page}" | grep -q "auditTimelineSection"
echo "Web Console retry/audit timeline sections found"

echo ""
echo "=== E2E Retry Loop 完成 ==="
final_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}")"
FINAL_JSON="${final_json}" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["FINAL_JSON"])
risk = data.get("risk_assessment") or {}
print(f"Incident:  {data['id']}")
print(f"Status:    {data.get('status')}")
print(f"Approval:  {data.get('approval_status')}")
print(f"AI Gen:    {data.get('ai_generated')}")
print(f"Retries:   {data.get('retry_count') or 0}")
print(f"Runbook:   {data.get('runbook_name')}")
print(f"Risk:      {risk.get('level', 'N/A')} (allowed={risk.get('allowed', 'N/A')})")
PY

echo "E2E Retry Loop passed: ${incident_id}"
