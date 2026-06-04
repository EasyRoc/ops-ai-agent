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
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )

        with patch("agent.api.v1.incidents.list_incidents", new=AsyncMock(return_value=[incident])):
            response = await list_incidents_endpoint(db=object())

        item = response["incidents"][0]
        self.assertEqual(item["runbook_name"], "cpu_high.md")
        self.assertEqual(item["approval_status"], "pending")
        self.assertEqual(item["risk_assessment"]["level"], "中风险")

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
        )

        with patch("agent.api.v1.incidents.get_incident", new=AsyncMock(return_value=incident)):
            response = await get_incident_endpoint("INC-PHASE2", db=object())

        self.assertEqual(response["runbook_name"], "cpu_high.md")
        self.assertEqual(response["action_plan"][0]["risk_level"], "中风险")
        self.assertEqual(response["approval_status"], "pending")
