import json
import unittest
import urllib.error
import base64
import hashlib
import time
import os
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from github_issue_approval import (
    DiscordApprovalProof, IssueCreationError, create_followup_issue, _approval_claims,
)


class Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.data


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return Response(item)


_PRIVATE_KEY = Ed25519PrivateKey.generate()
_PUBLIC_KEY = base64.b64encode(_PRIVATE_KEY.public_key().public_bytes(
    encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)).decode()
_APPROVER_ID = "12345678901234567"
_NOW = int(time.time())


def approval(**changes):
    values = dict(issuer="commitmentproof-discord-approval-bridge",
                  discord_user_id=_APPROVER_ID, action="create_github_issue",
                  draft_id="draft-42", target_repository="bh00mi-11/CommitmentProof",
                  draft_sha256=hashlib.sha256(
                      b"Collect deployment and QA evidence\nPlease provide linked deployment and QA evidence."
                  ).hexdigest(),
                  approved=True, idempotency_key="draft-42-followup-v1",
                  nonce="abcdefghijklmnop", issued_at=_NOW, expires_at=_NOW + 300,
                  signature="")
    values.update(changes)
    unsigned = DiscordApprovalProof(**values)
    signed = _PRIVATE_KEY.sign(json.dumps(_approval_claims(unsigned), sort_keys=True,
                                          separators=(",", ":")).encode())
    return DiscordApprovalProof(**{**values, "signature": base64.b64encode(signed).decode()})


def create(opener, record=None, repo="bh00mi-11/CommitmentProof"):
    with patch.dict(os.environ, {
        "COMMITMENTPROOF_APPROVAL_PUBLIC_KEY": _PUBLIC_KEY,
        "COMMITMENTPROOF_APPROVER_DISCORD_ID": _APPROVER_ID,
    }, clear=False):
        return create_followup_issue(
            approval=record or approval(), expected_draft_id="draft-42",
            target_repository=repo, title="Collect deployment and QA evidence",
            body="Please provide linked deployment and QA evidence.",
            token="test-secret", opener=opener, now=_NOW,
        )


class GitHubIssueApprovalTests(unittest.TestCase):
    def test_missing_approval_rejected_before_network(self):
        opener = Opener([])
        with self.assertRaisesRegex(IssueCreationError, "approval proof"):
            create_followup_issue(approval=None, expected_draft_id="draft-42",
                                 target_repository="bh00mi-11/CommitmentProof",
                                 title="t", body="b", token="secret", opener=opener)
        self.assertEqual(opener.requests, [])

    def test_denied_approval_rejected(self):
        with self.assertRaisesRegex(IssueCreationError, "does not authorize"):
            create(Opener([]), approval(approved=False))

    def test_wrong_target_rejected(self):
        with self.assertRaisesRegex(IssueCreationError, "does not match"):
            create(Opener([]), repo="someone/else")

    def test_forged_manually_constructed_approval_rejected(self):
        valid = approval()
        forged = DiscordApprovalProof(**{**valid.__dict__, "signature": base64.b64encode(b"x" * 64).decode()})
        opener = Opener([])
        with self.assertRaisesRegex(IssueCreationError, "signature is invalid"):
            create(opener, forged)
        self.assertEqual(opener.requests, [])

    def test_wrong_discord_user_rejected(self):
        with self.assertRaisesRegex(IssueCreationError, "different Discord user"):
            create(Opener([]), approval(discord_user_id="98765432109876543"))

    def test_stale_approval_rejected(self):
        stale = approval(issued_at=_NOW - 1200, expires_at=_NOW - 900)
        with self.assertRaisesRegex(IssueCreationError, "stale, expired"):
            create(Opener([]), stale)

    def test_wrong_draft_rejected(self):
        with self.assertRaisesRegex(IssueCreationError, "draft ID"):
            create(Opener([]), approval(draft_id="draft-other"))

    def test_modified_content_after_approval_rejected(self):
        opener = Opener([])
        with patch.dict(os.environ, {
            "COMMITMENTPROOF_APPROVAL_PUBLIC_KEY": _PUBLIC_KEY,
            "COMMITMENTPROOF_APPROVER_DISCORD_ID": _APPROVER_ID,
        }, clear=False):
            with self.assertRaisesRegex(IssueCreationError, "content does not match"):
                create_followup_issue(approval=approval(), expected_draft_id="draft-42",
                    target_repository="bh00mi-11/CommitmentProof", title="Changed title", body="Changed body",
                    token="test-secret", opener=opener, now=_NOW)
        self.assertEqual(opener.requests, [])

    def test_missing_identity_or_trust_configuration_rejected(self):
        with self.assertRaisesRegex(IssueCreationError, "identity is missing"):
            create(Opener([]), approval(discord_user_id=""))
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(IssueCreationError, "must be configured"):
                create_followup_issue(approval=approval(), expected_draft_id="draft-42",
                                     target_repository="bh00mi-11/CommitmentProof", title="t", body="b",
                                     token="secret", opener=Opener([]), now=_NOW)

    def test_duplicate_retry_returns_existing_issue_without_post(self):
        marker = "<!-- CommitmentProof idempotency-key: draft-42-followup-v1 -->"
        opener = Opener([[{"body": marker, "html_url": "https://github.com/bh00mi-11/CommitmentProof/issues/9", "number": 9}]])
        result = create(opener)
        self.assertEqual(result.status, "already_created")
        self.assertEqual(result.issue_number, 9)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(opener.requests[0].get_method(), "GET")

    def test_github_failure_is_safe_and_truthful(self):
        failure = urllib.error.HTTPError("https://api.github.com/x", 500, "server failed", {}, None)
        opener = Opener([[], failure])
        with self.assertRaisesRegex(IssueCreationError, "HTTP 500") as caught:
            create(opener)
        self.assertNotIn("test-secret", str(caught.exception))

    def test_success_creates_with_stable_marker_and_private_token_header(self):
        marker = "<!-- CommitmentProof idempotency-key: draft-42-followup-v1 -->"
        opener = Opener([[], {"html_url": "https://github.com/bh00mi-11/CommitmentProof/issues/10", "number": 10}])
        result = create(opener)
        self.assertEqual((result.status, result.issue_number), ("created", 10))
        self.assertEqual([request.get_method() for request in opener.requests], ["GET", "POST"])
        self.assertTrue(all(request.get_header("Authorization") == "Bearer test-secret" for request in opener.requests))
        payload = json.loads(opener.requests[-1].data)
        self.assertIn(marker, payload["body"])
        self.assertIn(_APPROVER_ID, payload["body"])
        self.assertNotIn("test-secret", repr(result))


if __name__ == "__main__":
    unittest.main()
