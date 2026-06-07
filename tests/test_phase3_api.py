from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch


class Phase3APITest(IsolatedAsyncioTestCase):
    async def test_report_endpoint_returns_markdown(self):
        from agent.api.v1.reports import get_report_endpoint

        report = type("ReportObj", (), {"incident_id": "INC-PHASE3", "content": "# Report", "created_at": None})()

        with patch("agent.api.v1.reports.get_report_by_incident", new=AsyncMock(return_value=report)):
            response = await get_report_endpoint("INC-PHASE3", format="markdown", db=object())

        self.assertEqual(response.body.decode(), "# Report")

    async def test_execution_list_endpoint_returns_execution_rows(self):
        from agent.api.v1.executions import list_incident_executions_endpoint

        execution = type(
            "ExecutionObj",
            (),
            {
                "id": 1,
                "incident_id": "INC-PHASE3",
                "action": "kubectl scale deployment order-service",
                "operator": "ou_test",
                "status": "success",
                "result": {"exit_code": 0},
                "round": 2,
                "ai_analysis": "扩容后进入验证",
                "created_at": None,
                "completed_at": None,
            },
        )()

        with patch("agent.api.v1.executions.list_executions_by_incident", new=AsyncMock(return_value=[execution])):
            response = await list_incident_executions_endpoint("INC-PHASE3", db=object())

        self.assertEqual(response["executions"][0]["status"], "success")
        self.assertEqual(response["executions"][0]["round"], 2)
        self.assertEqual(response["executions"][0]["ai_analysis"], "扩容后进入验证")
