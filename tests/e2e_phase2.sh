#!/usr/bin/env bash
# Phase 2 端到端测试：Runbook 匹配 → 风险评估 → 飞书审批闭环
# 测试流程：发告警 → 等待诊断+Runbook+风险评估 → 模拟飞书审批 → 验证状态更新
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
SERVICE="${SERVICE:-order-service}"
FINGERPRINT="phase2-$(date +%s)-$RANDOM"

echo "== Phase 2 E2E: Runbook + Approval =="
echo "Agent API: ${BASE_URL}"

# 记录测试前最新工单 ID，用于后续判断是否有新工单
before_latest_id="$(curl -fsS "${BASE_URL}/api/v1/incidents?limit=1" | python3 -c 'import json,sys; data=json.load(sys.stdin); incidents=data.get("incidents") or []; print(incidents[0]["id"] if incidents else "")')"

# 第1步：发送模拟 HighCPUUsage 告警到 Agent Webhook
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

# 第2步：轮询等待新工单出现（最长 120 秒）
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

# 第3步：等待诊断完成，确认 Runbook 已匹配、审批状态为 pending（最长 120 秒）
for _ in $(seq 1 60); do
  detail_json="$(curl -fsS "${BASE_URL}/api/v1/incidents/${incident_id}")"
  approval_status="$(printf '%s' "${detail_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("approval_status") or "")')"
  if [ "${approval_status}" = "pending" ]; then
    break
  fi
  sleep 2
done

# 校验 Runbook 名、处置方案、风险评估、审批状态等 Phase 2 核心字段
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

# 第4步：模拟飞书卡片回调 → 批准执行
curl -fsS -X POST "${BASE_URL}/api/v1/approvals/callback" \
  -H "Content-Type: application/json" \
  -d "{\"type\":\"card_action\",\"action\":{\"value\":{\"action\":\"approve\",\"incident_id\":\"${incident_id}\"}}}" >/dev/null

# 第5步：轮询等待审批状态更新为 approved（最长 20 秒）
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

# 第6步：验证 Web Console 首页和工单详情页可访问
curl -fsS "${BASE_URL}/" >/dev/null
curl -fsS "${BASE_URL}/incident-detail.html?id=${incident_id}" >/dev/null

echo "Phase 2 E2E passed: ${incident_id}"
