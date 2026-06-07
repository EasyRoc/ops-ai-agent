from unittest import TestCase

from agent.db.models import Execution, Incident


class IncidentModelTest(TestCase):
    def test_has_phase_d_observability_columns(self):
        columns = Incident.__table__.c

        self.assertIn("retry_count", columns)
        self.assertIn("retry_history", columns)
        self.assertIn("ai_generated", columns)
        self.assertIn("ai_reasoning", columns)

    def test_phase_d_incident_column_defaults(self):
        columns = Incident.__table__.c

        self.assertEqual(columns.retry_count.default.arg, 0)
        self.assertEqual(columns.ai_generated.default.arg, False)
        self.assertTrue(columns.retry_history.nullable)
        self.assertTrue(columns.ai_reasoning.nullable)


class ExecutionModelTest(TestCase):
    def test_has_phase_d_execution_columns(self):
        columns = Execution.__table__.c

        self.assertIn("round", columns)
        self.assertIn("ai_analysis", columns)

    def test_phase_d_execution_column_defaults(self):
        columns = Execution.__table__.c

        self.assertEqual(columns.round.default.arg, 1)
        self.assertTrue(columns.ai_analysis.nullable)
