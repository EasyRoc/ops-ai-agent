#!/usr/bin/env bash
# AI 兜底诊断端到端测试：未知告警 → AI 生成处置方案 → 飞书交互分支验证
# 该脚本需要 Agent、PostgreSQL、Redis、Prometheus、K8S demo 和 LLM 配置均可用。
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
SERVICE="${SERVICE:-order-service}"
ALERT_NAME="${AI_FALLBACK_ALERT_NAME:-DiskPressure}"
FINGERPRINT="ai-fallback-$(date +%s)-$RANDOM"

echo "== AI Fallback E2E: Unknown Alert -> AI Diagnosis =="
echo "Agent API: ${BASE_URL}"
echo "Alert: ${ALERT_NAME}, service: ${SERVICE}"

before_latest_id="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"

echo "[1/5] 发送未命中预置 Runbook 的告警"
curl -fsS -X POST "${BASE_URL}/api/v1/alerts" \
  -H "Content-Type: application/json" \
  -d "{
    \"receiver\": \"ai-fallback-e2e\",
    \"alerts\": [{
      \"status\": \"firing\",
      \"labels\": {
        \"alertname\": \"${ALERT_NAME}\",
        \"service\": \"${SERVICE}\",
        \"env\": \"prod\",
        \"severity\": \"P2\"
      },
      \"annotations\": {
        \"summary\": \"AI fallback E2E unknown alert\",
        \"value\": \"unknown alert should trigger AI fallback\"
      },
      \"startsAt\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
      \"fingerprint\": \"${FINGERPRINT}\"
    }]
  }" >/dev/null

echo "[2/5] 等待新工单创建"
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
  echo "FAIL: 未在规定时间内看到 AI 兜底测试工单"
  exit 1
fi
echo "Incident: ${incident_id}"

echo "[3/5] 等待 AI 兜底方案生成"
detail_json=""
for _ in $(seq 1 90); do
  detail_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}")"
  if DETAIL_JSON="${detail_json}" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["DETAIL_JSON"])
risk = data.get("risk_assessment") or {}
has_ai_plan = (
    data.get("runbook_name") == "ai_fallback"
    and risk.get("ai_generated") is True
    and bool(data.get("action_plan"))
    and data.get("approval_status") == "pending"
)
raise SystemExit(0 if has_ai_plan else 1)
PY
  then
    break
  fi
  sleep 2
done

DETAIL_JSON="${detail_json}" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["DETAIL_JSON"])
risk = data.get("risk_assessment") or {}
assert data.get("runbook_name") == "ai_fallback", data
assert risk.get("ai_generated") is True, data
assert data.get("action_plan"), data
assert data.get("approval_status") == "pending", data
assert risk.get("verification") or data.get("risk_assessment", {}).get("ai_reasoning") is not None, data
print("AI fallback runbook / risk / pending approval verified")
print(f"AI confidence: {risk.get('ai_confidence', 'N/A')}")
PY

echo "[4/5] 模拟飞书“我自己来”按钮，验证不会进入自动执行"
curl -fsS -X POST "${BASE_URL}/api/v1/approvals/callback" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"card_action\",\"operator\":{\"name\":\"ai-fallback-e2e\"},\"action\":{\"value\":{\"action\":\"manual_fix\",\"incident_id\":\"${incident_id}\"}}}" >/dev/null

echo "[5/5] 验证审批状态为 manual_executing"
approval_status=""
for _ in $(seq 1 20); do
  approval_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}/approval")"
  approval_status="$(printf '%s' "${approval_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("approval_status") or "")')"
  if [ "${approval_status}" = "manual_executing" ]; then
    break
  fi
  sleep 1
done

if [ "${approval_status}" != "manual_executing" ]; then
  echo "FAIL: AI 兜底工单审批状态未更新为 manual_executing，当前=${approval_status:-<empty>}"
  exit 1
fi

echo "AI Fallback E2E passed: ${incident_id}"
