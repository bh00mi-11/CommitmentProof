import unittest

from unittest.mock import patch

from frontend.backend import api_response, investigate


class FrontendBackendTests(unittest.TestCase):
    def test_live_adapter_returns_collector_sources_and_real_policy_decision(self):
        calls = []

        def collector(repository, number):
            calls.append((repository, number))
            return {
                "pr_merged": {"value": True, "source_url": "https://github.com/acme/app/pull/7"},
                "tests_passed": {"value": True, "source_url": "https://github.com/acme/app/commit/abc/checks"},
                "deployment_verified": {"value": None},
                "qa_confirmed": {"value": None},
                "collection_errors": [],
            }

        result = investigate({"repository": "acme/app", "pull_request_number": 7}, collector)
        self.assertEqual(calls, [("acme/app", 7)])
        self.assertEqual(result["assessment"]["decision"], "NEEDS_EVIDENCE")
        self.assertFalse(result["assessment"]["can_claim_complete"])
        self.assertEqual(result["evidence"]["pr_merged"]["source_url"], "https://github.com/acme/app/pull/7")
        self.assertEqual(result["evidence"]["deployment_verified"]["value"], None)

    def test_invalid_inputs_are_rejected_before_collector(self):
        def should_not_run(*_args):
            self.fail("collector should not run for invalid input")

        for payload in (
            {"repository": "not-a-repo", "pull_request_number": 7},
            {"repository": "acme/app", "pull_request_number": True},
            {"repository": "acme/app", "pull_request_number": 0},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                investigate(payload, should_not_run)

    def test_api_response_returns_json_safe_errors_and_rejects_unknown_routes(self):
        self.assertEqual(api_response("POST", "/api/investigate", b"{"), (400, {"error": "Request JSON is invalid"}))
        self.assertEqual(api_response("GET", "/api/investigate"), (405, {"error": "Method not allowed"}))
        self.assertEqual(api_response("POST", "/api/other", b"{}"), (404, {"error": "API route not found"}))
        self.assertEqual(api_response("GET", "/api/health"), (200, {"status": "ok"}))
        self.assertEqual(api_response("POST", "/api/investigate", b" " * 8193)[0], 413)

    def test_api_hides_collector_exception_details(self):
        with patch("frontend.backend.investigate", side_effect=RuntimeError("token=never-leak")):
            status, payload = api_response("POST", "/api/investigate", b'{"repository":"acme/app","pull_request_number":7}')
        self.assertEqual(status, 502)
        self.assertNotIn("never-leak", str(payload))


if __name__ == "__main__":
    unittest.main()
