import concurrent.futures
import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from discord_approval_service import ApprovalService, Draft
from github_issue_approval import IssueResult, _approval_claims


DISCORD_USER = "12345678901234567"
APPLICATION_ID = "23456789012345678"
DRAFT = Draft(
    draft_id="draft-42",
    target_repository="bh00mi-11/CommitmentProof",
    title="Collect deployment and QA evidence",
    body="Please provide linked deployment and QA evidence.",
)
NOW = 1_800_000_000


class DiscordApprovalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "approvals.sqlite3")
        self.discord_signer = Ed25519PrivateKey.generate()
        self.approval_signer = Ed25519PrivateKey.generate()
        self.call_lock = threading.Lock()
        self.issue_calls = []

        def issue_creator(**kwargs):
            with self.call_lock:
                self.issue_calls.append(kwargs)
                number = len(self.issue_calls)
            return IssueResult("created", f"https://github.com/bh00mi-11/CommitmentProof/issues/{number}", number)

        self.service = ApprovalService(
            drafts={DRAFT.draft_id: DRAFT},
            database_path=self.db,
            discord_public_key=self.discord_signer.public_key(),
            discord_application_id=APPLICATION_ID,
            allowed_discord_user_id=DISCORD_USER,
            approval_signing_key=self.approval_signer,
            issue_creator=issue_creator,
            clock=lambda: NOW,
        )

    def tearDown(self):
        self.temp.cleanup()

    def make_payload(self, *, interaction_id="33333333333333333", user_id=DISCORD_USER,
                     draft_id="draft-42", application_id=APPLICATION_ID):
        return {
            "type": 2,
            "application_id": application_id,
            "id": interaction_id,
            "token": "interaction-token-0123456789",
            "member": {"user": {"id": user_id}},
            "data": {"name": "review-followup", "options": [
                {"name": "draft_id", "value": draft_id},
            ]},
        }

    def make_component_payload(self, *, interaction_id="33333333333333333", user_id=DISCORD_USER,
                               decision="approve", application_id=APPLICATION_ID, custom_id=None):
        return {
            "type": 3,
            "application_id": application_id,
            "id": interaction_id,
            "token": "interaction-token-0123456789",
            "member": {"user": {"id": user_id}},
            "data": {"custom_id": custom_id or f"cp:{decision}:{DRAFT.idempotency_key}:1800000300:abcdefghijk"},
        }

    def signed_request(self, payload=None, *, timestamp=None, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(
            payload or self.make_payload(), separators=(",", ":")).encode()
        timestamp = str(NOW if timestamp is None else timestamp)
        signature = self.discord_signer.sign(timestamp.encode() + body).hex()
        return body, {
            "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": timestamp,
        }

    def approve(self, *, decision="approve", interaction_id="33333333333333333",
                user_id=DISCORD_USER, draft_id="draft-42", application_id=APPLICATION_ID):
        preview_body, preview_headers = self.signed_request(
            self.make_payload(user_id=user_id, draft_id=draft_id, application_id=application_id))
        preview = self.service.handle_request(preview_body, preview_headers)
        if preview.status_code != 200:
            return preview
        if "embeds" not in preview.payload.get("data", {}):
            return preview
        if preview.status_code == 200 and preview.payload.get("type") == 4:
            description = preview.payload["data"]["embeds"][0]["description"]
            self.assertIn(self.service._escape_preview(DRAFT.target_repository), description)
            self.assertIn(self.service._escape_preview(DRAFT.title), description)
        buttons = preview.payload["data"]["components"][0]["components"]
        custom_id = buttons[0 if decision == "approve" else 1]["custom_id"]
        click_body, click_headers = self.signed_request(self.make_component_payload(
            interaction_id=interaction_id, user_id=user_id, decision=decision,
            application_id=application_id, custom_id=custom_id))
        return self.service.handle_request(click_body, click_headers)

    def test_authenticated_discord_approval_binds_and_signs_exact_draft(self):
        response = self.approve()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.payload["type"], 6)
        result = self.service.execute(response.job)
        self.assertEqual(result.issue_url, "https://github.com/bh00mi-11/CommitmentProof/issues/1")
        self.assertEqual(len(self.issue_calls), 1)
        proof = self.issue_calls[0]["approval"]
        self.assertEqual(proof.discord_user_id, DISCORD_USER)
        self.assertEqual(proof.action, "create_github_issue")
        self.assertEqual(proof.draft_id, DRAFT.draft_id)
        self.assertEqual(proof.target_repository, DRAFT.target_repository)
        self.assertEqual(proof.draft_sha256, DRAFT.content_sha256)
        self.approval_signer.public_key().verify(
            base64.b64decode(proof.signature),
            json.dumps(_approval_claims(proof), sort_keys=True, separators=(",", ":")).encode(),
        )

    def test_forged_or_modified_discord_request_is_rejected(self):
        payload = self.make_payload()
        body, headers = self.signed_request(payload)
        altered = body.replace(b"draft-42", b"draft-43")
        response = self.service.handle_request(altered, headers)
        self.assertEqual(response.status_code, 401)
        self.assertIsNone(response.job)
        self.assertEqual(self.issue_calls, [])

    def test_wrong_user_is_rejected(self):
        response = self.approve(user_id="98765432109876543")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.job)

    def test_missing_authenticated_user_identity_is_rejected(self):
        payload = self.make_payload()
        payload["member"]["user"].pop("id")
        body, headers = self.signed_request(payload)
        response = self.service.handle_request(body, headers)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.job)

    def test_denial_is_recorded_without_github_write(self):
        response = self.approve(decision="deny")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.job)
        self.assertEqual(response.payload["data"]["content"], "Draft approval was denied.")
        self.assertEqual(self.issue_calls, [])

    def test_wrong_draft_is_rejected(self):
        response = self.approve(draft_id="draft-not-present")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.job)
        self.assertEqual(self.issue_calls, [])

    def test_duplicate_interaction_and_execution_do_not_repeat_write(self):
        preview_body, preview_headers = self.signed_request(self.make_payload())
        preview = self.service.handle_request(preview_body, preview_headers)
        button = preview.payload["data"]["components"][0]["components"][0]["custom_id"]
        component = self.make_component_payload(interaction_id="33333333333333333", custom_id=button)
        click_body, click_headers = self.signed_request(component)
        first = self.service.handle_request(click_body, click_headers)
        self.assertEqual(self.service.execute(first.job).status, "created")
        retry_same_interaction = self.service.handle_request(click_body, click_headers)
        self.assertIsNone(retry_same_interaction.job)
        self.assertIn("already created", retry_same_interaction.payload["data"]["content"])
        second_approval = self.approve(interaction_id="44444444444444444")
        self.assertEqual(self.service.execute(second_approval.job).status, "already_created")
        self.assertEqual(len(self.issue_calls), 1)

    def test_button_cannot_be_replayed_with_a_new_interaction(self):
        preview_body, preview_headers = self.signed_request(self.make_payload())
        preview = self.service.handle_request(preview_body, preview_headers)
        button = preview.payload["data"]["components"][0]["components"][0]["custom_id"]
        first_body, first_headers = self.signed_request(
            self.make_component_payload(interaction_id="33333333333333333", custom_id=button))
        first = self.service.handle_request(first_body, first_headers)
        self.service.execute(first.job)
        replay_body, replay_headers = self.signed_request(
            self.make_component_payload(interaction_id="44444444444444444", custom_id=button))
        replay = self.service.handle_request(replay_body, replay_headers)
        self.assertEqual(replay.status_code, 200)
        self.assertIn("already been used", replay.payload["data"]["content"])
        self.assertEqual(len(self.issue_calls), 1)

    def test_expired_approval_button_is_rejected(self):
        preview_body, preview_headers = self.signed_request(self.make_payload())
        preview = self.service.handle_request(preview_body, preview_headers)
        button = preview.payload["data"]["components"][0]["components"][0]["custom_id"]
        self.service.clock = lambda: NOW + 301
        body, headers = self.signed_request(
            self.make_component_payload(custom_id=button), timestamp=NOW + 301)
        response = self.service.handle_request(body, headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("expired or invalid", response.payload["data"]["content"])
        self.assertEqual(self.issue_calls, [])

    def test_concurrent_approvals_serialize_to_one_execution(self):
        original = self.service.issue_creator

        def slow_creator(**kwargs):
            time.sleep(0.08)
            return original(**kwargs)

        self.service.issue_creator = slow_creator
        jobs = [
            self.approve(interaction_id="33333333333333333").job,
            self.approve(interaction_id="44444444444444444").job,
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.service.execute, jobs))
        self.assertEqual({r.status for r in results}, {"created", "already_created"})
        self.assertEqual(len(self.issue_calls), 1)

    def test_discord_component_is_acknowledged_while_github_request_is_in_flight(self):
        first = self.approve(interaction_id="33333333333333333")
        entered = threading.Event()
        release = threading.Event()

        def blocked_creator(**kwargs):
            entered.set()
            release.wait(timeout=5)
            return IssueResult("created", "https://github.com/bh00mi-11/CommitmentProof/issues/5", 5)

        self.service.issue_creator = blocked_creator
        worker = threading.Thread(target=self.service.execute, args=(first.job,), daemon=True)
        worker.start()
        self.assertTrue(entered.wait(timeout=2))
        started = time.monotonic()
        try:
            concurrent_approval = self.approve(interaction_id="44444444444444444")
            elapsed = time.monotonic() - started
            self.assertEqual(concurrent_approval.payload["type"], 6)
            self.assertLess(elapsed, 2.5)
        finally:
            release.set()
            worker.join(timeout=3)

    def test_durable_accepted_approval_recovers_after_restart(self):
        response = self.approve()
        self.assertEqual(response.payload["type"], 6)
        recovered_calls = []

        def recovered_creator(**kwargs):
            recovered_calls.append(kwargs)
            return IssueResult("created", "https://github.com/bh00mi-11/CommitmentProof/issues/88", 88)

        restarted = ApprovalService(
            drafts={DRAFT.draft_id: DRAFT}, database_path=self.db,
            discord_public_key=self.discord_signer.public_key(),
            discord_application_id=APPLICATION_ID, allowed_discord_user_id=DISCORD_USER,
            approval_signing_key=self.approval_signer, issue_creator=recovered_creator,
            clock=lambda: NOW,
        )
        self.assertEqual(restarted.recover_pending(), 1)
        self.assertEqual(len(recovered_calls), 1)
        self.assertEqual(restarted.recover_pending(), 0)

    def test_restart_refuses_draft_changed_after_approval(self):
        self.approve()
        changed = Draft(DRAFT.draft_id, DRAFT.target_repository, DRAFT.title, "edited after approval")
        restarted_calls = []
        restarted = ApprovalService(
            drafts={changed.draft_id: changed}, database_path=self.db,
            discord_public_key=self.discord_signer.public_key(),
            discord_application_id=APPLICATION_ID, allowed_discord_user_id=DISCORD_USER,
            approval_signing_key=self.approval_signer,
            issue_creator=lambda **kwargs: restarted_calls.append(kwargs),
            clock=lambda: NOW,
        )
        self.assertEqual(restarted.recover_pending(), 0)
        self.assertEqual(restarted_calls, [])

    def test_stale_discord_request_and_wrong_application_are_rejected(self):
        payload = self.make_payload()
        body, headers = self.signed_request(payload, timestamp=NOW - 600)
        self.assertEqual(self.service.handle_request(body, headers).status_code, 401)
        self.assertEqual(self.approve(application_id="99999999999999999").status_code, 200)

    def test_slash_command_registration_manifest_matches_receiver(self):
        manifest = json.loads((Path(__file__).parents[1] / "discord_commands.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["name"], "review-followup")
        self.assertEqual(manifest[0]["options"], [{
            "name": "draft_id", "description": "Draft ID to preview before approval",
            "type": 3, "required": True,
        }])


if __name__ == "__main__":
    unittest.main()
