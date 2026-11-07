import io
import json
import unittest
import urllib.error

from commitment_proof import assess
from github_evidence import GitHubEvidenceError, collect_github_evidence


SHA = "a" * 40


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def pr(merged=True):
    return {"merged": merged, "head": {"sha": SHA}}


class GitHubEvidenceTests(unittest.TestCase):
    def test_collects_merged_pr_and_successful_checks(self):
        opener = FakeOpener([FakeResponse(pr()), FakeResponse({"total_count": 1, "check_runs": [{"conclusion": "success"}]})])
        result = collect_github_evidence("org/repo", 17, token="secret", opener=opener)
        self.assertEqual(result["pr_merged"], {"value": True, "source_url": "https://github.com/org/repo/pull/17"})
        self.assertEqual(result["tests_passed"], {"value": True, "source_url": f"https://github.com/org/repo/commit/{SHA}/checks"})
        self.assertIsNone(result["deployment_verified"]["value"])
        self.assertIsNone(result["qa_confirmed"]["value"])
        self.assertEqual(assess(result).decision, "NEEDS_EVIDENCE")
        self.assertTrue(all(r.headers["Authorization"] == "Bearer secret" for r, _ in opener.requests))
        self.assertNotIn("secret", repr(result))

    def test_failed_check_is_negative_evidence(self):
        opener = FakeOpener([FakeResponse(pr()), FakeResponse({"check_runs": [{"conclusion": "failure"}]})])
        result = collect_github_evidence("org/repo", 17, opener=opener)
        self.assertIs(result["tests_passed"]["value"], False)
        self.assertEqual(assess(result).decision, "BLOCKED")

    def test_unfinished_or_missing_checks_are_unknown(self):
        for checks in ({"check_runs": [{"conclusion": None}]}, {"check_runs": []}):
            with self.subTest(checks=checks):
                opener = FakeOpener([FakeResponse(pr()), FakeResponse(checks)])
                result = collect_github_evidence("org/repo", 17, opener=opener)
                self.assertIsNone(result["tests_passed"]["value"])
                self.assertTrue(result["collection_errors"])

    def test_follows_pagination(self):
        next_url = "https://api.github.com/repos/org/repo/commits/" + SHA + "/check-runs?per_page=100&page=2"
        opener = FakeOpener([
            FakeResponse(pr()),
            FakeResponse({"check_runs": [{"conclusion": "success"}]}, {"Link": f'<{next_url}>; rel="next"'}),
            FakeResponse({"check_runs": [{"conclusion": "success"}]}),
        ])
        result = collect_github_evidence("org/repo", 17, opener=opener)
        self.assertIs(result["tests_passed"]["value"], True)
        self.assertEqual(len(opener.requests), 3)

    def test_api_errors_fail_closed_and_do_not_leak_token(self):
        error = urllib.error.HTTPError("https://api.github.com/x", 401, "Unauthorized", {}, io.BytesIO(b"secret body"))
        opener = FakeOpener([error])
        result = collect_github_evidence("org/repo", 17, token="top-secret", opener=opener)
        self.assertIsNone(result["pr_merged"]["value"])
        self.assertIn("authentication failed", result["collection_errors"][0])
        self.assertNotIn("top-secret", repr(result))
        self.assertEqual(assess(result).decision, "NEEDS_EVIDENCE")

    def test_rate_limit_and_network_errors_are_safe(self):
        rate_error = urllib.error.HTTPError("https://api.github.com/x", 403, "Forbidden", {"X-RateLimit-Remaining": "0"}, io.BytesIO())
        network_error = urllib.error.URLError("connection detail")
        for first, expected in ((rate_error, "rate limit"), (network_error, "network error")):
            with self.subTest(expected=expected):
                result = collect_github_evidence("org/repo", 17, opener=FakeOpener([first]))
                self.assertIn(expected, result["collection_errors"][0])

    def test_rejects_invalid_inputs(self):
        with self.assertRaises(ValueError):
            collect_github_evidence("https://github.com/org/repo", 1, opener=FakeOpener([]))
        with self.assertRaises(ValueError):
            collect_github_evidence("org/repo", 0, opener=FakeOpener([]))


if __name__ == "__main__":
    unittest.main()
