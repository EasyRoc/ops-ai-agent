from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agent.db.models import AuditLog


class AuditApiTest(IsolatedAsyncioTestCase):
    async def test_list_audit_logs_returns_chronological_items(self):
        from agent.api.v1.audit import list_audit_logs_endpoint

        logs = [
            AuditLog(
                id=1,
                incident_id="INC-1",
                actor="system",
                action="ai_plan_generated",
                detail={"confidence": 0.75},
                created_at=datetime(2026, 6, 7, 10, 0, 0, tzinfo=timezone.utc),
            ),
            AuditLog(
                id=2,
                incident_id="INC-1",
                actor="ou_test",
                action="retry_executed",
                detail={"round": 2},
                created_at=datetime(2026, 6, 7, 10, 5, 0, tzinfo=timezone.utc),
            ),
        ]
        result = MagicMock()
        result.scalars.return_value.all.return_value = logs
        db = AsyncMock()
        db.execute.return_value = result

        response = await list_audit_logs_endpoint("INC-1", db=db)

        self.assertEqual(response["incident_id"], "INC-1")
        self.assertEqual(response["total"], 2)
        self.assertEqual(response["audit_logs"][0]["action"], "ai_plan_generated")
        self.assertEqual(response["audit_logs"][1]["action"], "retry_executed")
        self.assertEqual(response["audit_logs"][0]["created_at"], "2026-06-07T10:00:00+00:00")

    async def test_list_audit_logs_returns_empty_list(self):
        from agent.api.v1.audit import list_audit_logs_endpoint

        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        db = AsyncMock()
        db.execute.return_value = result

        response = await list_audit_logs_endpoint("INC-999", db=db)

        self.assertEqual(response["total"], 0)
        self.assertEqual(response["audit_logs"], [])
