"""GitHub issue creation gated by a signed Discord approval proof.

Switch/Codex message text is not an authenticated approval source. The proof
must be signed by a separately operated Discord approval bridge using Ed25519;
only its public key is configured in this process.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import base64
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

API_ROOT = "https://api.github.com"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
_DISCORD_ID_RE = re.compile(r"^[0-9]{17,20}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_EXPECTED_ISSUER = "commitmentproof-discord-approval-bridge"
_MAX_APPROVAL_AGE_SECONDS = 600
_MAX_FUTURE_SKEW_SECONDS = 30


class IssueCreationError(RuntimeError):
    """Safe issue creation error; never includes credentials or response body."""


@dataclass(frozen=True)
class DiscordApprovalProof:
    """Claims and Ed25519 signature issued by the trusted Discord bridge."""

    issuer: str
    discord_user_id: str
    action: str
    draft_id: str
    draft_sha256: str
    target_repository: str
    approved: bool
    idempotency_key: str
    nonce: str
    issued_at: int
    expires_at: int
    signature: str


@dataclass(frozen=True)
class IssueResult:
    status: str  # "created" or "already_created"
    issue_url: str
    issue_number: int


def _approval_claims(proof: DiscordApprovalProof) -> dict[str, Any]:
    return {
        "action": proof.action,
        "approved": proof.approved,
        "discord_user_id": proof.discord_user_id,
        "draft_id": proof.draft_id,
        "draft_sha256": proof.draft_sha256,
        "expires_at": proof.expires_at,
        "idempotency_key": proof.idempotency_key,
        "issued_at": proof.issued_at,
        "issuer": proof.issuer,
        "nonce": proof.nonce,
        "target_repository": proof.target_repository,
    }


def _validate_approval(
    proof: DiscordApprovalProof, expected_draft_id: str, target_repository: str,
    public_key_b64: str | None, expected_discord_id: str | None, now: int | None = None,
) -> None:
    if not isinstance(proof, DiscordApprovalProof):
        raise IssueCreationError("Authenticated Discord approval proof is required")
    if not isinstance(public_key_b64, str) or not isinstance(expected_discord_id, str):
        raise IssueCreationError("Trusted approval public key and approver Discord ID must be configured")
    if not isinstance(expected_draft_id, str) or not expected_draft_id.strip():
        raise IssueCreationError("Expected draft ID is required")
    if not isinstance(target_repository, str) or not _REPO_RE.fullmatch(target_repository):
        raise IssueCreationError("Target repository must be in owner/name form")
    if any(not isinstance(value, str) for value in (
            proof.issuer, proof.discord_user_id, proof.action, proof.draft_id,
            proof.draft_sha256, proof.target_repository, proof.idempotency_key,
            proof.nonce, proof.signature)):
        raise IssueCreationError("Approval proof contains malformed fields")
    if not _DISCORD_ID_RE.fullmatch(expected_discord_id):
        raise IssueCreationError("Configured approver Discord identity is invalid")
    if not _DISCORD_ID_RE.fullmatch(proof.discord_user_id):
        raise IssueCreationError("Authenticated Discord user identity is missing or invalid")
    if proof.discord_user_id != expected_discord_id:
        raise IssueCreationError("Approval was signed for a different Discord user")
    if proof.issuer != _EXPECTED_ISSUER:
        raise IssueCreationError("Approval proof issuer is not trusted")
    if proof.action != "create_github_issue" or proof.approved is not True:
        raise IssueCreationError("Approval proof does not authorize this action")
    if proof.draft_id != expected_draft_id or not expected_draft_id:
        raise IssueCreationError("Approval draft ID does not match the requested draft")
    if not re.fullmatch(r"[0-9a-f]{64}", proof.draft_sha256):
        raise IssueCreationError("Approval draft content digest is invalid")
    if proof.target_repository != target_repository:
        raise IssueCreationError("Approval target repository does not match the requested repository")
    if not _KEY_RE.fullmatch(proof.idempotency_key):
        raise IssueCreationError("A stable idempotency key is required")
    if not _NONCE_RE.fullmatch(proof.nonce):
        raise IssueCreationError("Approval nonce is missing or invalid")
    current = int(time.time()) if now is None else now
    if (isinstance(proof.issued_at, bool) or not isinstance(proof.issued_at, int)
            or isinstance(proof.expires_at, bool) or not isinstance(proof.expires_at, int)
            or proof.issued_at > current + _MAX_FUTURE_SKEW_SECONDS
            or proof.expires_at <= current or proof.expires_at <= proof.issued_at
            or proof.expires_at - proof.issued_at > _MAX_APPROVAL_AGE_SECONDS
            or current - proof.issued_at > _MAX_APPROVAL_AGE_SECONDS):
        raise IssueCreationError("Approval proof is stale, expired, or has invalid timestamps")
    try:
        raw_key = base64.b64decode(public_key_b64, validate=True)
        signature = base64.b64decode(proof.signature, validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(raw_key)
        canonical = json.dumps(_approval_claims(proof), sort_keys=True, separators=(",", ":")).encode("utf-8")
        public_key.verify(signature, canonical)
    except (ValueError, InvalidSignature, TypeError):
        raise IssueCreationError("Approval signature is invalid") from None


def _request(url: str, method: str, token: str, opener: Callable[..., Any], payload: dict[str, Any] | None = None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "CommitmentProof-approved-issue-creator",
        "Authorization": f"Bearer {token}",
    }
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener(request, timeout=20) as response:
            raw = response.read()
        return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if method == "POST" and exc.code >= 500:
            raise IssueCreationError(f"GitHub API returned HTTP {exc.code}; issue creation outcome may be unknown") from None
        if exc.code == 401:
            raise IssueCreationError("GitHub authentication failed") from None
        if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
            raise IssueCreationError("GitHub API rate limit reached") from None
        raise IssueCreationError(f"GitHub API returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        suffix = "; creation outcome may be unknown" if method == "POST" else ""
        raise IssueCreationError(f"GitHub request failed due to a network error{suffix}") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        suffix = "; creation outcome may be unknown" if method == "POST" else ""
        raise IssueCreationError(f"GitHub returned an invalid response{suffix}") from None


def create_followup_issue(
    *, approval: DiscordApprovalProof, expected_draft_id: str, target_repository: str,
    title: str, body: str, token: str | None = None,
    opener: Callable[..., Any] | None = None, max_pages: int = 20,
    now: int | None = None,
) -> IssueResult:
    """Create an issue only after verifying a signed, narrowly scoped approval.

    A stable marker is scanned across all existing issues before POST. This
    prevents ordinary sequential retries from duplicating an issue. GitHub has
    no create-issue idempotency primitive, so concurrent calls still require
    external serialization by the caller. The bridge's private signing key
    must never be available to this process; configure only its public key and
    the independently approved Discord user ID.
    """
    # Trust anchors are deployment configuration, never caller-supplied values.
    approval_public_key = os.environ.get("COMMITMENTPROOF_APPROVAL_PUBLIC_KEY")
    expected_approver_discord_id = os.environ.get("COMMITMENTPROOF_APPROVER_DISCORD_ID")
    _validate_approval(approval, expected_draft_id, target_repository,
                       approval_public_key, expected_approver_discord_id, now)
    if not title.strip() or not body.strip():
        raise IssueCreationError("Issue title and body are required")
    draft_digest = hashlib.sha256(f"{title.rstrip()}\n{body.rstrip()}".encode("utf-8")).hexdigest()
    if approval.draft_sha256 != draft_digest:
        raise IssueCreationError("Approved draft content does not match the requested draft")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise IssueCreationError("max_pages must be a positive integer")
    token = token if token is not None else os.environ.get("GITHUB_TOKEN")
    if not token:
        raise IssueCreationError("GITHUB_TOKEN is required")
    opener = opener or urllib.request.urlopen
    repository = urllib.parse.quote(target_repository, safe="/")
    marker = f"<!-- CommitmentProof idempotency-key: {approval.idempotency_key} -->"
    list_url = f"{API_ROOT}/repos/{repository}/issues?state=all&per_page=100"
    complete_scan = False
    for page in range(1, max_pages + 1):
        url = f"{list_url}&page={page}"
        issues = _request(url, "GET", token, opener)
        if not isinstance(issues, list):
            raise IssueCreationError("GitHub returned an unexpected issues list")
        for issue in issues:
            if isinstance(issue, dict) and marker in str(issue.get("body") or ""):
                issue_url, number = issue.get("html_url"), issue.get("number")
                if isinstance(issue_url, str) and issue_url.startswith("https://github.com/") and isinstance(number, int):
                    return IssueResult("already_created", issue_url, number)
                raise IssueCreationError("An issue with this idempotency key exists but its result is malformed")
        if len(issues) < 100:
            complete_scan = True
            break
    if not complete_scan:
        raise IssueCreationError("Issue scan reached its page limit; refusing to risk a duplicate")

    issue_body = (
        f"{body.rstrip()}\n\nApproval proof: draft `{approval.draft_id}`, "
        f"Discord user `{approval.discord_user_id}`, action `{approval.action}`, "
        f"repository `{approval.target_repository}`, nonce hash `{hashlib.sha256(approval.nonce.encode()).hexdigest()}`.\n"
        f"{marker}"
    )
    created = _request(f"{API_ROOT}/repos/{repository}/issues", "POST", token, opener,
                       {"title": title, "body": issue_body})
    if (not isinstance(created, dict) or not isinstance(created.get("html_url"), str)
            or not created["html_url"].startswith("https://github.com/")
            or isinstance(created.get("number"), bool) or not isinstance(created.get("number"), int)):
        raise IssueCreationError("GitHub response was incomplete; issue may have been created")
    return IssueResult("created", created["html_url"], created["number"])
