import unittest

from frontend.backend import investigate


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


if __name__ == "__main__":
    unittest.main()
