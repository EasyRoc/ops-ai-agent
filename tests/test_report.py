from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch


class IncidentReportTest(TestCase):
    def test_build_markdown_report_contains_phase3_sections(self):
        from agent.agents.report import build_markdown_report

        report = build_markdown_report(
            {
                "incident_id": "INC-PHASE3",
                "diagnosis": {"root_cause": "CPU 过高", "confidence": 0.8, "evidence": ["CPU > 90%"]},
                "runbook": {"name": "cpu_high.md", "steps": [{"description": "扩容", "command": "kubectl scale deployment order-service"}]},
                "execution_result": {"status": "success"},
                "verification_result": {"recovered": True},
            },
            summary={"summary": "服务已恢复", "impact": "order-service", "suggestions": ["优化容量"]},
        )

        self.assertIn("## 根因", report)
        self.assertIn("## 执行结果", report)
        self.assertIn("## 验证结果", report)
        self.assertIn("服务已恢复", report)


class ReportNodeTest(IsolatedAsyncioTestCase):
    async def test_generate_incident_report_persists_report_and_updates_state(self):
        from agent.agents.report import generate_incident_report

        state = {
            "incident_id": "INC-PHASE3",
            "alert_parsed": {"alertname": "HighCPUUsage", "service": "order-service"},
            "diagnosis": {"root_cause": "CPU 过高", "confidence": 0.8, "evidence": ["CPU > 90%"]},
            "runbook": {"name": "cpu_high.md", "steps": []},
            "execution_result": {"status": "success"},
            "verification_result": {"recovered": True},
            "report": None,
        }

        with (
            patch("agent.agents.report.summarize_with_llm", new=AsyncMock(return_value={"summary": "服务已恢复", "impact": "order-service", "suggestions": ["补充压测"]})),
            patch("agent.agents.report.save_report", new=AsyncMock()) as save_report,
            patch("agent.agents.report.write_audit", new=AsyncMock()),
            patch("agent.agents.report._mark_incident_resolved_if_needed", new=AsyncMock()),
        ):
            result = await generate_incident_report(state)

        self.assertEqual(result["report"]["incident_id"], "INC-PHASE3")
        self.assertIn("服务已恢复", result["report"]["content"])
        save_report.assert_awaited_once()
