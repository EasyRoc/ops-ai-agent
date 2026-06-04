from pathlib import Path
from unittest import TestCase


class WebConsoleTest(TestCase):
    def test_web_console_files_are_present_and_reference_phase2_api(self):
        root = Path(__file__).resolve().parents[1]
        index = root / "web" / "index.html"
        detail = root / "web" / "incident-detail.html"

        self.assertTrue(index.exists())
        self.assertTrue(detail.exists())
        self.assertIn("/api/v1/incidents", index.read_text(encoding="utf-8"))
        detail_text = detail.read_text(encoding="utf-8")
        self.assertIn("/api/v1/approvals/callback", detail_text)
        self.assertIn("pending_approval", detail_text)
