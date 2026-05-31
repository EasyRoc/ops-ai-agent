import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agent.tools import kubernetes, loki, prometheus


class _LocalAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/v1/query?"):
            body = {"status": "success", "data": {"result": []}}
        elif self.path.startswith("/loki/api/v1/query_range?"):
            body = {"status": "success", "data": {"result": []}}
        elif self.path.startswith("/api/v1/namespaces/demo/pods?"):
            body = {"items": []}
        else:
            self.send_error(404)
            return

        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


class LocalHTTPClientsTest(IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalAPIHandler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def proxy_environment(self):
        return patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://127.0.0.1:1",
                "NO_PROXY": "",
                "http_proxy": "http://127.0.0.1:1",
                "no_proxy": "",
            },
        )

    async def test_prometheus_queries_bypass_environment_proxy(self):
        with self.proxy_environment(), patch.object(
            prometheus, "PROM_API", f"{self.base_url}/api/v1"
        ):
            self.assertEqual(await prometheus._query("up"), [])

    async def test_loki_queries_bypass_environment_proxy(self):
        with self.proxy_environment(), patch.object(
            loki, "LOKI_API", f"{self.base_url}/loki/api/v1"
        ):
            self.assertEqual(await loki.query_service_logs("order-service"), [])

    async def test_kubernetes_queries_bypass_environment_proxy(self):
        with self.proxy_environment(), patch.object(
            kubernetes, "K8S_API", self.base_url
        ):
            self.assertEqual(
                await kubernetes.get_service_pods("order-service"),
                {"total": 0, "ready": 0, "pods": []},
            )
