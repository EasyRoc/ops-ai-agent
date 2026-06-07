from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agent.api.v1.incidents import get_incident_endpoint, list_incidents_endpoint
from agent.db.models import Incident


class IncidentAPITest(IsolatedAsyncioTestCase):
    async def test_incident_list_includes_phase2_fields(self):
        incident = Incident(
            id="INC-PHASE2",
            service="order-service",
            env="prod",
            severity="P1",
            status="pending_approval",
            alert_name="HighCPUUsage",
            root_cause="CPU 异常升高",
            confidence=0.8,
            runbook_name="cpu_high.md",
            action_plan=[{"risk_level": "中风险", "description": "扩容", "command": "kubectl scale deployment order-service -n demo --replicas=4"}],
            risk_assessment={"level": "中风险", "score": 40},
            approval_status="pending",
            ai_generated=True,
            retry_count=2,
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        incident.ai_reasoning = "AI 判断为容量不足"
        incident.retry_history = [{"round": 1, "analysis": "扩容未恢复"}]

        with patch("agent.api.v1.incidents.list_incidents", new=AsyncMock(return_value=[incident])):
            response = await list_incidents_endpoint(db=object())

        item = response["incidents"][0]
        self.assertEqual(item["runbook_name"], "cpu_high.md")
        self.assertEqual(item["approval_status"], "pending")
        self.assertEqual(item["risk_assessment"]["level"], "中风险")
        self.assertTrue(item["ai_generated"])
        self.assertEqual(item["retry_count"], 2)

    async def test_incident_detail_includes_action_plan(self):
        incident = Incident(
            id="INC-PHASE2",
            service="order-service",
            env="prod",
            severity="P1",
            status="pending_approval",
            alert_name="HighCPUUsage",
            runbook_name="cpu_high.md",
            action_plan=[{"risk_level": "中风险", "description": "扩容", "command": "kubectl scale deployment order-service -n demo --replicas=4"}],
            risk_assessment={"level": "中风险", "score": 40},
            approval_status="pending",
            ai_generated=True,
            ai_reasoning="AI 判断为 CPU 热点",
            retry_count=1,
            retry_history=[{"round": 1, "analysis": "扩容未恢复"}],
        )

        with patch("agent.api.v1.incidents.get_incident", new=AsyncMock(return_value=incident)):
            response = await get_incident_endpoint("INC-PHASE2", db=object())

        self.assertEqual(response["runbook_name"], "cpu_high.md")
        self.assertEqual(response["action_plan"][0]["risk_level"], "中风险")
        self.assertEqual(response["approval_status"], "pending")
        self.assertTrue(response["ai_generated"])
        self.assertEqual(response["ai_reasoning"], "AI 判断为 CPU 热点")
        self.assertEqual(response["retry_count"], 1)
        self.assertEqual(response["retry_history"][0]["round"], 1)
