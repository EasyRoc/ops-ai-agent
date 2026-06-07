from pathlib import Path
from unittest import TestCase


class WebConsoleTest(TestCase):
    def test_web_console_files_are_present_and_reference_phase2_api(self):
        root = Path(__file__).resolve().parents[1]
        index = root / "web" / "index.html"
        detail = root / "web" / "incident-detail.html"
        executions = root / "web" / "executions.html"
        reports = root / "web" / "reports.html"

        self.assertTrue(index.exists())
        self.assertTrue(detail.exists())
        self.assertTrue(executions.exists())
        self.assertTrue(reports.exists())
        self.assertIn("/api/v1/incidents", index.read_text(encoding="utf-8"))
        self.assertIn("/executions.html", index.read_text(encoding="utf-8"))
        self.assertIn("/reports.html", index.read_text(encoding="utf-8"))
        index_text = index.read_text(encoding="utf-8")
        self.assertIn("statAiRatio", index_text)
        self.assertIn("statAiSuccess", index_text)
        self.assertIn("ai_generated", index_text)
        detail_text = detail.read_text(encoding="utf-8")
        self.assertIn("/api/v1/approvals/callback", detail_text)
        self.assertIn("/api/v1/incidents/${encodeURIComponent(incidentId)}/executions", detail_text)
        self.assertIn("/api/v1/reports/${encodeURIComponent(incidentId)}", detail_text)
        self.assertIn("/api/v1/incidents/${encodeURIComponent(incidentId)}/audit", detail_text)
        self.assertIn("retryTimelineSection", detail_text)
        self.assertIn("loadRetryTimeline", detail_text)
        self.assertIn("pending_approval", detail_text)
        self.assertIn("/api/v1/executions", executions.read_text(encoding="utf-8"))
        self.assertIn("/api/v1/reports", reports.read_text(encoding="utf-8"))
