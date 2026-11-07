import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from api.health import handler as HealthHandler
from api.investigate import handler as InvestigateHandler


class VercelApiTests(unittest.TestCase):
    def request(self, handler, method, path, body=None):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            encoded = None if body is None else json.dumps(body)
            connection.request(method, path, body=encoded, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read()
            headers = {key.lower(): value for key, value in response.getheaders()}
            connection.close()
            return response.status, headers, json.loads(raw)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_vercel_investigation_route_returns_existing_policy_schema(self):
        expected = {
            "repository": "acme/app",
            "pull_request_number": 7,
            "evidence": {"pr_merged": {"value": True}},
            "assessment": {"decision": "NEEDS_EVIDENCE", "can_claim_complete": False},
            "collected_at": "2026-11-07T00:00:00+00:00",
        }
        with patch("frontend.backend.investigate", return_value=expected) as investigate_mock:
            status, headers, payload = self.request(
                InvestigateHandler,
                "POST",
                "/api/investigate",
                {"repository": "acme/app", "pull_request_number": 7},
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        self.assertTrue(headers["content-type"].startswith("application/json"))
        self.assertEqual(headers["cache-control"], "no-store")
        investigate_mock.assert_called_once_with({"repository": "acme/app", "pull_request_number": 7})

    def test_vercel_health_route_is_json_and_unknown_api_method_is_json(self):
        status, headers, payload = self.request(HealthHandler, "GET", "/api/health")
        self.assertEqual((status, payload), (200, {"status": "ok"}))
        self.assertTrue(headers["content-type"].startswith("application/json"))
        status, headers, payload = self.request(InvestigateHandler, "GET", "/api/investigate")
        self.assertEqual((status, payload), (405, {"error": "Method not allowed"}))
        self.assertEqual(headers["allow"], "POST")

    def test_vercel_investigation_route_returns_json_for_invalid_input(self):
        status, headers, payload = self.request(
            InvestigateHandler,
            "POST",
            "/api/investigate",
            {"repository": "not-valid", "pull_request_number": 7},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": "Repository must use owner/name form"})
        self.assertTrue(headers["content-type"].startswith("application/json"))


if __name__ == "__main__":
    unittest.main()
