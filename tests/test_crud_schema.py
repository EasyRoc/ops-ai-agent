from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch


class EnsurePhase4SchemaTest(IsolatedAsyncioTestCase):
    async def test_executes_all_phase4_migration_statements(self):
        from agent.db.crud import ensure_phase4_schema

        mock_connection = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__.return_value = mock_connection

        with patch("agent.db.crud.engine", mock_engine):
            await ensure_phase4_schema()

        statements = [str(call.args[0]) for call in mock_connection.execute.call_args_list]
        self.assertIn("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0", statements)
        self.assertIn("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS retry_history JSONB", statements)
        self.assertIn("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_generated BOOLEAN DEFAULT FALSE", statements)
        self.assertIn("ALTER TABLE incidents ADD COLUMN IF NOT EXISTS ai_reasoning TEXT", statements)
        self.assertIn("ALTER TABLE executions ADD COLUMN IF NOT EXISTS round INTEGER DEFAULT 1", statements)
        self.assertIn("ALTER TABLE executions ADD COLUMN IF NOT EXISTS ai_analysis TEXT", statements)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_incidents_ai_generated ON incidents(ai_generated)", statements)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_executions_incident_round ON executions(incident_id, round)", statements)


class MigrateRetryDataTest(IsolatedAsyncioTestCase):
    async def test_migrates_retry_data_from_risk_assessment(self):
        from agent.db.crud import migrate_retry_data
        from agent.db.models import Incident

        incident = Incident(id="INC-TEST", service="order-service", env="prod", severity="P1")
        incident.retry_count = 0
        incident.ai_generated = True
        incident.risk_assessment = {
            "level": "中风险",
            "retry": {
                "count": 3,
                "history": [{"round": 1, "analysis": "扩容未恢复"}],
            },
        }

        result = MagicMock()
        result.scalars.return_value.all.return_value = [incident]
        mock_session = AsyncMock()
        mock_session.execute.return_value = result
        mock_session_context = MagicMock()
        mock_session_context.__aenter__.return_value = mock_session

        with patch("agent.db.crud.AsyncSessionLocal", return_value=mock_session_context):
            migrated = await migrate_retry_data()

        self.assertEqual(migrated, 1)
        self.assertEqual(incident.retry_count, 3)
        self.assertEqual(len(incident.retry_history), 1)
        self.assertEqual(incident.retry_history[0]["round"], 1)
        mock_session.commit.assert_awaited_once()

    async def test_skips_when_no_retry_meta(self):
        from agent.db.crud import migrate_retry_data
        from agent.db.models import Incident

        incident = Incident(id="INC-TEST", service="order-service", env="prod", severity="P1")
        incident.retry_count = 0
        incident.ai_generated = True
        incident.risk_assessment = {"level": "中风险"}

        result = MagicMock()
        result.scalars.return_value.all.return_value = [incident]
        mock_session = AsyncMock()
        mock_session.execute.return_value = result
        mock_session_context = MagicMock()
        mock_session_context.__aenter__.return_value = mock_session

        with patch("agent.db.crud.AsyncSessionLocal", return_value=mock_session_context):
            migrated = await migrate_retry_data()

        self.assertEqual(migrated, 0)
        self.assertEqual(incident.retry_count, 0)
        self.assertIsNone(incident.retry_history)
        mock_session.commit.assert_awaited_once()
