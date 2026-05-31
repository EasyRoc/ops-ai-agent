from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agent.tools import prometheus


class PrometheusToolTest(IsolatedAsyncioTestCase):
    async def test_cpu_query_uses_process_cpu_gauge(self):
        query = AsyncMock(return_value=[])

        with patch.object(prometheus, "_query", new=query):
            await prometheus.query_service_metrics("order-service")

        self.assertEqual(
            query.await_args_list[0].args[0],
            'max(process_cpu_usage{service="order-service"}) * 100',
        )
