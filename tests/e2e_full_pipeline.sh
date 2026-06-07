#!/usr/bin/env bash
# E2E: 未知告警 -> AI 兜底 -> 批准执行 -> 执行/重试 -> 审计 -> 报告/Web Console
# 这是更完整的联调脚本，依赖真实 Agent、DB、Redis、Prometheus/K8S demo 和 LLM。
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
SERVICE="${SERVICE:-order-service}"
ALERT_NAME="${FULL_PIPELINE_ALERT_NAME:-ThreadPoolExhausted}"
FINGERPRINT="full-pipeline-$(date +%s)-$RANDOM"

echo "== Full Pipeline E2E: Unknown Alert -> AI Fallback -> Retry -> Report =="
echo "Agent API: ${BASE_URL}"

curl -fsS "${BASE_URL}/health" >/dev/null || {
  echo "FAIL: Agent 不可达，请先启动服务: ${BASE_URL}/health"
  exit 1
}

before_latest_id="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"

echo "[1/7] 发送未知告警"
curl -fsS -X POST "${BASE_URL}/api/v1/alerts" \
  -H "Content-Type: application/json" \
  -d "{
    \"receiver\": \"full-pipeline-e2e\",
    \"alerts\": [{
      \"status\": \"firing\",
      \"labels\": {
        \"alertname\": \"${ALERT_NAME}\",
        \"service\": \"${SERVICE}\",
        \"env\": \"prod\",
        \"severity\": \"P2\"
      },
      \"annotations\": {
        \"summary\": \"Full pipeline E2E test\",
        \"value\": \"unknown alert should trigger AI fallback\"
      },
      \"startsAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"fingerprint\": \"${FINGERPRINT}\"
    }]
  }" >/dev/null

echo "[2/7] 等待 AI 兜底方案"
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
ok = data.get("runbook_name") == "ai_fallback" and risk.get("ai_generated") is True and data.get("approval_status") == "pending"
sys.exit(0 if ok else 1)
PY
    then
      incident_id="${latest_id}"
      break
    fi
  fi
  sleep 2
done

if [ -z "${incident_id}" ]; then
  echo "FAIL: 未等到 AI 兜底工单"
  exit 1
fi
echo "Incident: ${incident_id}"

echo "[3/7] 批准 AI 自动执行"
curl -fsS -X POST "${BASE_URL}/api/v1/approvals/callback" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"card_action\",\"operator\":{\"name\":\"full-pipeline-e2e\"},\"action\":{\"value\":{\"action\":\"approve_ai\",\"incident_id\":\"${incident_id}\"}}}" >/dev/null

echo "[4/7] 等待执行记录"
for _ in $(seq 1 90); do
  executions_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/executions")"
  exec_count="$(printf '%s' "${executions_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(len(data.get("executions") or []))')"
  if [ "${exec_count}" -gt 0 ]; then
    break
  fi
  sleep 2
done

EXECUTIONS_JSON="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/executions")"
EXECUTIONS_JSON="${EXECUTIONS_JSON}" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["EXECUTIONS_JSON"])
executions = data.get("executions") or []
assert executions, "should have execution records"
for item in executions:
    assert "round" in item, item
print(f"Execution records verified: {len(executions)}")
PY

echo "[5/7] 验证审计日志"
AUDIT_JSON="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/audit?limit=100")"
AUDIT_JSON="${AUDIT_JSON}" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["AUDIT_JSON"])
actions = [item.get("action") for item in data.get("audit_logs") or []]
assert "ai_plan_generated" in actions, actions
assert any(action in actions for action in ("retry_executed", "retry_command_executed", "retry_recovery_verified", "retry_recovery_verify_failed")), actions
print(f"Audit actions verified: {actions}")
PY

echo "[6/7] 尝试等待故障报告（恢复后应生成；未恢复时允许跳过）"
report_found="false"
for _ in $(seq 1 60); do
  if curl -fsS "${BASE_URL}/api/v1/reports/${incident_id}" >/tmp/full-pipeline-report.json 2>/dev/null; then
    report_found="true"
    break
  fi
  sleep 2
done
if [ "${report_found}" = "true" ]; then
  python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("/tmp/full-pipeline-report.json").read_text())
content = data.get("content") or ""
assert "## 执行结果" in content, content
print("Report verified")
PY
else
  echo "WARNING: 未生成报告，可能是恢复验证未通过并进入重试/人工升级；继续验证页面与状态"
fi

echo "[7/7] 验证 Web Console"
curl -fsS "${BASE_URL}/" >/dev/null
curl -fsS "${BASE_URL}/incident-detail.html?id=${incident_id}" | grep -q "retryTimelineSection"

echo "Full Pipeline E2E finished: ${incident_id} (report_found=${report_found})"
