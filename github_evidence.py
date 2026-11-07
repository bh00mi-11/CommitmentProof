"""Read-only GitHub pull-request and check-run evidence collector.

Uses GitHub's REST API v3 and the standard library. Set ``GITHUB_TOKEN`` to
authenticate; the token is sent only as an Authorization header and is never
included in output or error messages.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

API_ROOT = "https://api.github.com"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")


class GitHubEvidenceError(RuntimeError):
    """An API failure, with a safe message that never contains credentials."""


def _request_json(url: str, token: str | None, opener: Callable[..., Any]) -> tuple[Any, dict[str, str]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "CommitmentProof-read-only-evidence",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener(request, timeout=20) as response:
            raw = response.read()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
        try:
            return json.loads(raw.decode("utf-8")), response_headers
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubEvidenceError("GitHub returned invalid JSON") from exc
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or (exc.code == 403 and (exc.headers.get("Retry-After") or exc.headers.get("X-RateLimit-Remaining") == "0")):
            raise GitHubEvidenceError("GitHub API rate limit reached") from None
        if exc.code == 401:
            raise GitHubEvidenceError("GitHub authentication failed; check GITHUB_TOKEN") from None
        if exc.code == 404:
            raise GitHubEvidenceError("GitHub repository or pull request was not found or is inaccessible") from None
        raise GitHubEvidenceError(f"GitHub API returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubEvidenceError("GitHub API request failed due to a network error") from None


def _next_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        match = re.match(r'\s*<([^>]+)>\s*;\s*rel="next"', part)
        if match:
            return match.group(1)
    return None


def _get_all(url: str, token: str | None, opener: Callable[..., Any], max_pages: int) -> list[Any]:
    results: list[Any] = []
    seen: set[str] = set()
    page_url: str | None = url
    for _ in range(max_pages):
        if page_url is None:
            return results
        if page_url in seen:
            raise GitHubEvidenceError("GitHub pagination loop detected")
        if not page_url.startswith(API_ROOT + "/"):
            raise GitHubEvidenceError("GitHub returned an unsafe pagination link")
        seen.add(page_url)
        payload, headers = _request_json(page_url, token, opener)
        if isinstance(payload, dict) and isinstance(payload.get("check_runs"), list):
            payload = payload["check_runs"]
        if not isinstance(payload, list):
            raise GitHubEvidenceError("GitHub returned an unexpected list response")
        results.extend(payload)
        page_url = _next_link(headers.get("link"))
    if page_url is not None:
        raise GitHubEvidenceError(f"GitHub pagination exceeded the {max_pages}-page safety limit")
    return results


def collect_github_evidence(
    repository: str,
    pull_request_number: int,
    *,
    token: str | None = None,
    opener: Callable[..., Any] | None = None,
    max_pages: int = 20,
) -> dict[str, Any]:
    """Collect source-linked PR/check evidence compatible with ``assess``.

    ``repository`` must be ``owner/name``. Unknown or unavailable information
    is represented by ``None``; deployment and QA are always unknown because
    this collector does not independently verify them. Collection errors are
    returned in the additional ``collection_errors`` field.
    """
    if not isinstance(repository, str) or not _REPO_RE.fullmatch(repository):
        raise ValueError("repository must be in owner/name form")
    if isinstance(pull_request_number, bool) or not isinstance(pull_request_number, int) or pull_request_number < 1:
        raise ValueError("pull_request_number must be a positive integer")
    if not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError("max_pages must be a positive integer")

    token = token if token is not None else os.environ.get("GITHUB_TOKEN")
    opener = opener or urllib.request.urlopen
    encoded_repo = urllib.parse.quote(repository, safe="/")
    pr_url = f"{API_ROOT}/repos/{encoded_repo}/pulls/{pull_request_number}"
    pr_page_url = f"https://github.com/{encoded_repo}/pull/{pull_request_number}"
    unknown = {"value": None}
    evidence: dict[str, Any] = {
        "pr_merged": dict(unknown),
        "tests_passed": dict(unknown),
        "deployment_verified": dict(unknown),
        "qa_confirmed": dict(unknown),
        "collection_errors": [],
    }

    head_sha: str | None = None
    try:
        pr, _ = _request_json(pr_url, token, opener)
        if not isinstance(pr, dict) or not isinstance(pr.get("merged"), bool):
            raise GitHubEvidenceError("GitHub returned incomplete pull-request data")
        evidence["pr_merged"] = {"value": pr["merged"], "source_url": pr_page_url}
        head = pr.get("head")
        candidate_sha = head.get("sha") if isinstance(head, dict) else None
        if isinstance(candidate_sha, str) and _SHA_RE.fullmatch(candidate_sha):
            head_sha = candidate_sha
        else:
            evidence["collection_errors"].append("Pull request head commit is missing or invalid; checks were not fetched")
    except GitHubEvidenceError as exc:
        evidence["collection_errors"].append(str(exc))

    if head_sha is not None:
        checks_url = f"{API_ROOT}/repos/{encoded_repo}/commits/{head_sha}/check-runs?per_page=100"
        try:
            checks = _get_all(checks_url, token, opener, max_pages)
            if not checks:
                evidence["collection_errors"].append("No GitHub check runs were found for the pull request head commit")
            elif not all(isinstance(check, dict) for check in checks):
                evidence["collection_errors"].append("GitHub returned malformed check-run data")
            else:
                statuses = [check.get("conclusion") for check in checks]
                all_success = all(status == "success" for status in statuses)
                any_failure = any(status in {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"} for status in statuses)
                check_url = f"https://github.com/{encoded_repo}/commit/{head_sha}/checks"
                value = False if any_failure else (True if all_success else None)
                evidence["tests_passed"] = {"value": value, "source_url": check_url}
                if value is None:
                    evidence["collection_errors"].append("One or more check runs are queued, in progress, skipped, neutral, or have an unknown conclusion")
        except GitHubEvidenceError as exc:
            evidence["collection_errors"].append(str(exc))

    return evidence


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Collect read-only GitHub PR and check-run evidence")
    parser.add_argument("repository", help="GitHub repository in owner/name form")
    parser.add_argument("pull_request_number", type=int, help="Pull request number")
    args = parser.parse_args()
    print(json.dumps(collect_github_evidence(args.repository, args.pull_request_number), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
