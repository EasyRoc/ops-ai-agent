from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch


class AuditTest(IsolatedAsyncioTestCase):
    async def test_write_audit_uses_audit_log_model(self):
        from agent.agents.audit import write_audit

        with (
            patch("agent.agents.audit.AsyncSessionLocal") as session_factory,
            patch("agent.agents.audit.create_audit_log", new=AsyncMock()) as create_log,
        ):
            session_factory.return_value.__aenter__.return_value = object()
            await write_audit("INC-PHASE3", "operator", "approved", {"source": "test"})

        audit_log = create_log.await_args.args[1]
        self.assertEqual(audit_log.incident_id, "INC-PHASE3")
        self.assertEqual(audit_log.actor, "operator")
        self.assertEqual(audit_log.action, "approved")


class RBACTest(IsolatedAsyncioTestCase):
    async def test_rbac_blocks_execute_for_viewer(self):
        from agent.middleware.auth import rbac_middleware

        request = SimpleNamespace(
            method="POST",
            url=SimpleNamespace(path="/api/v1/incidents/INC-1/execute"),
            headers={"x-user-role": "viewer"},
        )

        response = await rbac_middleware(request, AsyncMock())

        self.assertEqual(response.status_code, 403)

    async def test_rbac_allows_operator_execute(self):
        from agent.middleware.auth import rbac_middleware

        request = SimpleNamespace(
            method="POST",
            url=SimpleNamespace(path="/api/v1/incidents/INC-1/execute"),
            headers={"x-user-role": "operator"},
        )
        call_next = AsyncMock(return_value="ok")

        response = await rbac_middleware(request, call_next)

        self.assertEqual(response, "ok")
        call_next.assert_awaited_once_with(request)


class FaultDBTest(TestCase):
    def test_score_runbook_handles_empty_history(self):
        from agent.agents.fault_db import calculate_runbook_score

        self.assertEqual(calculate_runbook_score([]), {"total": 0, "success": 0, "score": 0})
