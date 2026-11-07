"""Private Discord interaction receiver for approved GitHub follow-up issues.

Run this service outside model-controlled execution. Discord's request
signature authenticates the interaction; the service then checks the allowed
Discord account, resolves an immutable local draft, signs the approval proof,
and executes the GitHub write under a SQLite serialization lock.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from github_issue_approval import (
    DiscordApprovalProof,
    IssueCreationError,
    IssueResult,
    _approval_claims,
    create_followup_issue,
)

_DISCORD_ID_RE = re.compile(r"^[0-9]{17,20}$")
_INTERACTION_ID_RE = re.compile(r"^[0-9]{17,20}$")
_DRAFT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_BODY_BYTES = 64 * 1024
_MAX_DISCORD_SKEW_SECONDS = 300


class ApprovalServiceError(RuntimeError):
    """Safe operational error that excludes request tokens and credentials."""


@dataclass(frozen=True)
class Draft:
    draft_id: str
    target_repository: str
    title: str
    body: str
    action: str = "create_github_issue"

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(f"{self.title.rstrip()}\n{self.body.rstrip()}".encode("utf-8")).hexdigest()

    @property
    def idempotency_key(self) -> str:
        binding = f"{self.draft_id}\n{self.target_repository}\n{self.action}\n{self.content_sha256}"
        return hashlib.sha256(binding.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExecutionJob:
    interaction_id: str
    interaction_token: str
    draft: Draft
    discord_user_id: str


@dataclass(frozen=True)
class InteractionResponse:
    status_code: int
    payload: dict[str, Any]
    job: ExecutionJob | None = None


def load_drafts(path: str | os.PathLike[str]) -> Mapping[str, Draft]:
    """Load the immutable draft catalog once at startup; there is no write API."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ApprovalServiceError("Draft catalog could not be read") from None
    if not isinstance(raw, dict) or set(raw) != {"drafts"} or not isinstance(raw["drafts"], list):
        raise ApprovalServiceError("Draft catalog must contain only a drafts list")
    drafts: dict[str, Draft] = {}
    for item in raw["drafts"]:
        if not isinstance(item, dict) or set(item) != {"draft_id", "target_repository", "title", "body"}:
            raise ApprovalServiceError("Each draft must define draft_id, target_repository, title, and body")
        draft_id, repository, title, body = (item[k] for k in ("draft_id", "target_repository", "title", "body"))
        if (not isinstance(draft_id, str) or not _DRAFT_ID_RE.fullmatch(draft_id)
                or not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
                or not isinstance(title, str) or not title.strip()
                or not isinstance(body, str) or not body.strip()):
            raise ApprovalServiceError("Draft catalog contains invalid fields")
        if draft_id in drafts:
            raise ApprovalServiceError("Draft IDs must be unique")
        drafts[draft_id] = Draft(draft_id, repository, title, body)
    return MappingProxyType(drafts)


def _ephemeral(text: str) -> dict[str, Any]:
    return {"type": 4, "data": {"content": text, "flags": 64}}


class ApprovalService:
    def __init__(
        self,
        *,
        drafts: Mapping[str, Draft],
        database_path: str | os.PathLike[str],
        discord_public_key: Ed25519PublicKey,
        discord_application_id: str,
        allowed_discord_user_id: str,
        approval_signing_key: Ed25519PrivateKey,
        issue_creator: Callable[..., IssueResult] = create_followup_issue,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(discord_application_id, str) or not _DISCORD_ID_RE.fullmatch(discord_application_id):
            raise ValueError("discord_application_id must be a Discord snowflake")
        if not isinstance(allowed_discord_user_id, str) or not _DISCORD_ID_RE.fullmatch(allowed_discord_user_id):
            raise ValueError("allowed_discord_user_id must be a Discord snowflake")
        self.drafts = MappingProxyType(dict(drafts))
        self.database_path = str(database_path)
        self.discord_public_key = discord_public_key
        self.discord_application_id = discord_application_id
        self.allowed_discord_user_id = allowed_discord_user_id
        self.approval_signing_key = approval_signing_key
        self.issue_creator = issue_creator
        self.clock = clock
        self._schema_lock = threading.Lock()
        self._initialize_database()

    @classmethod
    def from_environment(cls) -> "ApprovalService":
        required = (
            "DISCORD_PUBLIC_KEY", "DISCORD_APPLICATION_ID", "COMMITMENTPROOF_APPROVER_DISCORD_ID",
            "COMMITMENTPROOF_APPROVAL_PUBLIC_KEY", "COMMITMENTPROOF_APPROVAL_SIGNING_PRIVATE_KEY",
            "GITHUB_TOKEN", "APPROVAL_DRAFTS_PATH", "APPROVAL_DB_PATH",
        )
        values = {name: os.environ.get(name) for name in required}
        if any(not value for value in values.values()):
            raise ApprovalServiceError("Required service configuration is missing")
        try:
            discord_key_bytes = bytes.fromhex(values["DISCORD_PUBLIC_KEY"] or "")
            discord_public_key = Ed25519PublicKey.from_public_bytes(discord_key_bytes)
            signing_raw = base64.b64decode(values["COMMITMENTPROOF_APPROVAL_SIGNING_PRIVATE_KEY"] or "", validate=True)
            signing_key = Ed25519PrivateKey.from_private_bytes(signing_raw)
            approval_public_key = base64.b64decode(values["COMMITMENTPROOF_APPROVAL_PUBLIC_KEY"] or "", validate=True)
            derived_public_key = signing_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
            if derived_public_key != approval_public_key:
                raise ValueError("approval signing key mismatch")
            drafts = load_drafts(values["APPROVAL_DRAFTS_PATH"] or "")
        except (ValueError, TypeError):
            raise ApprovalServiceError("Service cryptographic configuration is invalid") from None
        return cls(
            drafts=drafts, database_path=values["APPROVAL_DB_PATH"] or "",
            discord_public_key=discord_public_key,
            discord_application_id=values["DISCORD_APPLICATION_ID"] or "",
            allowed_discord_user_id=values["COMMITMENTPROOF_APPROVER_DISCORD_ID"] or "",
            approval_signing_key=signing_key,
        )

    def _connect(self) -> sqlite3.Connection:
        # Keep Discord's three-second callback deadline bounded even under DB contention.
        connection = sqlite3.connect(self.database_path, timeout=1, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 1000")
        return connection

    def _initialize_database(self) -> None:
        with self._schema_lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS interactions (
                        interaction_id TEXT PRIMARY KEY,
                        discord_user_id TEXT NOT NULL,
                        draft_id TEXT NOT NULL,
                        target_repository TEXT,
                        draft_sha256 TEXT,
                        draft_title TEXT,
                        draft_body TEXT,
                        action TEXT,
                        preview_nonce TEXT,
                        decision TEXT NOT NULL,
                        state TEXT NOT NULL,
                        issue_url TEXT,
                        issue_number INTEGER,
                        created_at INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS executions (
                        idempotency_key TEXT PRIMARY KEY,
                        draft_id TEXT NOT NULL,
                        target_repository TEXT NOT NULL,
                        draft_sha256 TEXT NOT NULL,
                        state TEXT NOT NULL,
                        lease_until INTEGER,
                        issue_url TEXT,
                        issue_number INTEGER,
                        last_error TEXT,
                        updated_at INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS preview_tokens (
                        nonce TEXT PRIMARY KEY,
                        idempotency_key TEXT NOT NULL,
                        expires_at INTEGER NOT NULL,
                        consumed_by_interaction_id TEXT
                    );
                """)
                columns = {row[1] for row in connection.execute("PRAGMA table_info(interactions)")}
                for column in ("target_repository", "draft_sha256", "draft_title", "draft_body", "action", "preview_nonce"):
                    if column not in columns:
                        connection.execute(f"ALTER TABLE interactions ADD COLUMN {column} TEXT")
                execution_columns = {row[1] for row in connection.execute("PRAGMA table_info(executions)")}
                if "lease_until" not in execution_columns:
                    connection.execute("ALTER TABLE executions ADD COLUMN lease_until INTEGER")
                if self.database_path != ":memory:":
                    try:
                        os.chmod(self.database_path, 0o600)
                    except OSError:
                        # Windows ACLs are provisioned by deployment; never widen permissions here.
                        pass
            finally:
                connection.close()

    def handle_request(self, raw_body: bytes, headers: Mapping[str, str]) -> InteractionResponse:
        if len(raw_body) > _MAX_BODY_BYTES:
            return InteractionResponse(413, _ephemeral("Request body is too large."))
        signature = headers.get("X-Signature-Ed25519") or headers.get("x-signature-ed25519")
        timestamp = headers.get("X-Signature-Timestamp") or headers.get("x-signature-timestamp")
        if not signature or not timestamp or not re.fullmatch(r"[0-9]{1,16}", timestamp):
            return InteractionResponse(401, _ephemeral("Discord signature is required."))
        if abs(int(self.clock()) - int(timestamp)) > _MAX_DISCORD_SKEW_SECONDS:
            return InteractionResponse(401, _ephemeral("Discord request timestamp is stale."))
        try:
            signature_bytes = bytes.fromhex(signature)
            self.discord_public_key.verify(signature_bytes, timestamp.encode("ascii") + raw_body)
        except (ValueError, InvalidSignature):
            return InteractionResponse(401, _ephemeral("Discord request signature is invalid."))
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return InteractionResponse(400, _ephemeral("Request JSON is invalid."))
        if not isinstance(payload, dict):
            return InteractionResponse(400, _ephemeral("Request payload is invalid."))
        if payload.get("type") == 1:
            return InteractionResponse(200, {"type": 1})
        if payload.get("type") == 3:
            return self._handle_component(payload)
        return self._handle_command(payload)

    def _interaction_identity(self, payload: dict[str, Any]) -> tuple[str, str, str] | InteractionResponse:
        if payload.get("application_id") != self.discord_application_id:
            return InteractionResponse(200, _ephemeral("This interaction is not authorized."))
        interaction_id = payload.get("id")
        interaction_token = payload.get("token")
        if not isinstance(interaction_id, str) or not _INTERACTION_ID_RE.fullmatch(interaction_id):
            return InteractionResponse(200, _ephemeral("Interaction ID is invalid."))
        if not isinstance(interaction_token, str) or not re.fullmatch(r"[A-Za-z0-9._-]{20,256}", interaction_token):
            return InteractionResponse(200, _ephemeral("Interaction token is invalid."))
        member = payload.get("member")
        user = member.get("user") if isinstance(member, dict) else payload.get("user")
        user_id = user.get("id") if isinstance(user, dict) else None
        if not isinstance(user_id, str) or not _DISCORD_ID_RE.fullmatch(user_id):
            return InteractionResponse(200, _ephemeral("Authenticated Discord user identity is missing."))
        if user_id != self.allowed_discord_user_id:
            return InteractionResponse(200, _ephemeral("This Discord account is not authorized to approve issues."))
        return interaction_id, interaction_token, user_id

    @staticmethod
    def _escape_preview(value: str) -> str:
        escaped = re.sub(r"([\\*_{}\[\]()#+\-.!|>~])", r"\\\1", value)
        escaped = escaped.replace(chr(96), chr(92) + chr(96))
        return re.sub(r"@(everyone|here)", "@\u200b\\1", escaped, flags=re.IGNORECASE)

    def _handle_command(self, payload: dict[str, Any]) -> InteractionResponse:
        if payload.get("type") != 2:
            return InteractionResponse(200, _ephemeral("This interaction is not authorized."))
        identity = self._interaction_identity(payload)
        if isinstance(identity, InteractionResponse):
            return identity
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("name") != "review-followup":
            return InteractionResponse(200, _ephemeral("Unsupported approval command."))
        options = data.get("options")
        if not isinstance(options, list) or len(options) != 1 or not isinstance(options[0], dict):
            return InteractionResponse(200, _ephemeral("Specify one draft ID to review."))
        if options[0].get("name") != "draft_id":
            return InteractionResponse(200, _ephemeral("Specify one draft ID to review."))
        draft_id = options[0].get("value")
        if not isinstance(draft_id, str) or not _DRAFT_ID_RE.fullmatch(draft_id):
            return InteractionResponse(200, _ephemeral("Draft ID is invalid."))
        draft = self.drafts.get(draft_id)
        if draft is None:
            return InteractionResponse(200, _ephemeral("That draft does not exist."))
        tick = chr(96)
        description = (
            f"Repository: {tick}{self._escape_preview(draft.target_repository)}{tick}\n"
            f"Action: create GitHub issue\n\n"
            f"**Title**\n{self._escape_preview(draft.title)}\n\n"
            f"**Body**\n{self._escape_preview(draft.body)}\n\n"
            f"Draft content SHA-256: {tick}{draft.content_sha256}{tick}"
        )
        if len(draft.title) > 256 or len(description) > 4000:
            return InteractionResponse(200, _ephemeral("This draft is too large to review safely in Discord."))
        key = draft.idempotency_key
        preview_nonce = secrets.token_urlsafe(8)
        expires_at = int(self.clock()) + 300
        connection = self._connect()
        try:
            connection.execute("DELETE FROM preview_tokens WHERE expires_at <= ?", (int(self.clock()),))
            connection.execute(
                "INSERT INTO preview_tokens(nonce, idempotency_key, expires_at) VALUES (?, ?, ?)",
                (preview_nonce, key, expires_at))
        except sqlite3.Error:
            return InteractionResponse(200, _ephemeral("Approval service storage is unavailable; please retry the review command."))
        finally:
            connection.close()
        response = {
            "type": 4,
            "data": {
                "embeds": [{"title": f"Review draft {draft.draft_id}", "description": description}],
                "components": [{"type": 1, "components": [
                    {"type": 2, "style": 3, "label": "Approve issue", "custom_id": f"cp:approve:{key}:{expires_at}:{preview_nonce}"},
                    {"type": 2, "style": 4, "label": "Deny", "custom_id": f"cp:deny:{key}:{expires_at}:{preview_nonce}"},
                ]}],
                "allowed_mentions": {"parse": []},
                "flags": 64,
            },
        }
        return InteractionResponse(200, response)

    def _handle_component(self, payload: dict[str, Any]) -> InteractionResponse:
        identity = self._interaction_identity(payload)
        if isinstance(identity, InteractionResponse):
            return identity
        interaction_id, interaction_token, user_id = identity
        data = payload.get("data")
        custom_id = data.get("custom_id") if isinstance(data, dict) else None
        if not isinstance(custom_id, str):
            return InteractionResponse(200, _ephemeral("Approval button is invalid or expired."))
        match = re.fullmatch(r"cp:(approve|deny):([0-9a-f]{64}):([0-9]{10}):([A-Za-z0-9_-]{8,16})", custom_id or "")
        if not match:
            return InteractionResponse(200, _ephemeral("Approval button is invalid or expired."))
        decision, binding_key, expiry_text, preview_nonce = match.groups()
        expires_at = int(expiry_text)
        matches = [draft for draft in self.drafts.values() if draft.idempotency_key == binding_key]
        if len(matches) != 1:
            return InteractionResponse(200, _ephemeral("The reviewed draft changed or is no longer available."))
        draft = matches[0]
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT discord_user_id, draft_id, decision, state, issue_url, issue_number, "
                "target_repository, draft_sha256, draft_title, draft_body, action, preview_nonce "
                "FROM interactions WHERE interaction_id = ?", (interaction_id,)).fetchone()
            if existing:
                expected = (user_id, draft.draft_id, decision, draft.target_repository,
                            draft.content_sha256, draft.title, draft.body, draft.action, preview_nonce)
                actual = (existing["discord_user_id"], existing["draft_id"], existing["decision"],
                          existing["target_repository"], existing["draft_sha256"], existing["draft_title"],
                          existing["draft_body"], existing["action"], existing["preview_nonce"])
                if actual != expected:
                    connection.execute("ROLLBACK")
                    return InteractionResponse(200, _ephemeral("Interaction ID was already used for different content."))
                connection.execute("COMMIT")
                if decision == "deny":
                    return InteractionResponse(200, _ephemeral("Draft approval was denied."))
                if existing["state"] == "completed":
                    return InteractionResponse(200, _ephemeral(f"Issue already created: {existing['issue_url']}"))
                return InteractionResponse(200, {"type": 6},
                    ExecutionJob(interaction_id, interaction_token, draft, user_id))
            preview = connection.execute(
                "SELECT idempotency_key, expires_at, consumed_by_interaction_id FROM preview_tokens WHERE nonce=?",
                (preview_nonce,)).fetchone()
            if (not preview or preview["idempotency_key"] != binding_key
                    or preview["expires_at"] != expires_at or expires_at <= int(self.clock())):
                connection.execute("ROLLBACK")
                return InteractionResponse(200, _ephemeral("This approval preview is expired or invalid."))
            if preview["consumed_by_interaction_id"] is not None:
                connection.execute("ROLLBACK")
                return InteractionResponse(200, _ephemeral("This approval button has already been used."))
            connection.execute("UPDATE preview_tokens SET consumed_by_interaction_id=? WHERE nonce=?",
                               (interaction_id, preview_nonce))
            initial_state = "denied" if decision == "deny" else "accepted"
            connection.execute(
                "INSERT INTO interactions(interaction_id, discord_user_id, draft_id, target_repository, "
                "draft_sha256, draft_title, draft_body, action, preview_nonce, decision, state, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (interaction_id, user_id, draft.draft_id, draft.target_repository, draft.content_sha256,
                 draft.title, draft.body, draft.action, preview_nonce, decision, initial_state, int(self.clock())))
            connection.execute("COMMIT")
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            return InteractionResponse(200, _ephemeral("Approval service storage is unavailable; please review and approve again."))
        finally:
            connection.close()
        if decision == "deny":
            return InteractionResponse(200, _ephemeral("Draft approval was denied."))
        return InteractionResponse(200, {"type": 6},
            ExecutionJob(interaction_id, interaction_token, draft, user_id))

    def _make_proof(self, job: ExecutionJob) -> DiscordApprovalProof:
        issued_at = int(self.clock())
        unsigned = DiscordApprovalProof(
            issuer="commitmentproof-discord-approval-bridge",
            discord_user_id=job.discord_user_id,
            action=job.draft.action,
            draft_id=job.draft.draft_id,
            draft_sha256=job.draft.content_sha256,
            target_repository=job.draft.target_repository,
            approved=True,
            idempotency_key=job.draft.idempotency_key,
            nonce=secrets.token_urlsafe(24),
            issued_at=issued_at,
            expires_at=issued_at + 300,
            signature="",
        )
        canonical = json.dumps(_approval_claims(unsigned), sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = base64.b64encode(self.approval_signing_key.sign(canonical)).decode("ascii")
        return DiscordApprovalProof(**{**unsigned.__dict__, "signature": signature})

    def execute(self, job: ExecutionJob) -> IssueResult:
        """Claim work briefly in SQLite, do network I/O unlocked, then persist.

        The lease serializes service processes sharing the SQLite file without
        holding a database write lock across GitHub requests. Its 450-second
        window exceeds the collector's bounded API request time.
        """
        draft = job.draft
        deadline = time.monotonic() + 480
        lease_seconds = 450
        while True:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT state, issue_url, issue_number, lease_until FROM executions WHERE idempotency_key = ?",
                    (draft.idempotency_key,)).fetchone()
                if row and row["state"] == "completed":
                    result = IssueResult("already_created", row["issue_url"], row["issue_number"])
                    connection.execute(
                        "UPDATE interactions SET state='completed', issue_url=?, issue_number=? WHERE interaction_id=?",
                        (result.issue_url, result.issue_number, job.interaction_id))
                    connection.execute("COMMIT")
                    return result
                now = int(self.clock())
                if row and row["state"] == "processing" and row["lease_until"] and row["lease_until"] > now:
                    connection.execute("COMMIT")
                    if time.monotonic() >= deadline:
                        raise ApprovalServiceError("Another approved request is still processing; retry status check")
                    time.sleep(0.1)
                    continue
                connection.execute(
                    "INSERT INTO executions(idempotency_key, draft_id, target_repository, draft_sha256, state, lease_until, updated_at) "
                    "VALUES (?, ?, ?, ?, 'processing', ?, ?) ON CONFLICT(idempotency_key) DO UPDATE SET "
                    "state='processing', lease_until=excluded.lease_until, last_error=NULL, updated_at=excluded.updated_at",
                    (draft.idempotency_key, draft.draft_id, draft.target_repository,
                     draft.content_sha256, now + lease_seconds, now))
                connection.execute("COMMIT")
            except sqlite3.Error:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise ApprovalServiceError("Approval service storage is unavailable") from None
            finally:
                connection.close()
            break

        try:
            result = self.issue_creator(
                approval=self._make_proof(job), expected_draft_id=draft.draft_id,
                target_repository=draft.target_repository, title=draft.title, body=draft.body,
            )
            if (not isinstance(result, IssueResult) or not isinstance(result.issue_url, str)
                    or not result.issue_url.startswith("https://github.com/")
                    or isinstance(result.issue_number, bool) or not isinstance(result.issue_number, int)):
                raise IssueCreationError("GitHub issue creator returned an invalid result")
        except (IssueCreationError, OSError, TimeoutError):
            safe_error = "issue creation failed; outcome may be unknown"
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE executions SET state='failed', lease_until=NULL, last_error=?, updated_at=? "
                    "WHERE idempotency_key=?",
                    (safe_error, int(self.clock()), draft.idempotency_key))
                connection.execute(
                    "UPDATE interactions SET state='failed' WHERE interaction_id=?", (job.interaction_id,))
                connection.execute("COMMIT")
            except sqlite3.Error:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise ApprovalServiceError("Approval service storage is unavailable; GitHub outcome may be unknown") from None
            finally:
                connection.close()
            raise ApprovalServiceError(safe_error) from None

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE executions SET state='completed', lease_until=NULL, issue_url=?, issue_number=?, "
                "last_error=NULL, updated_at=? WHERE idempotency_key=?",
                (result.issue_url, result.issue_number, int(self.clock()), draft.idempotency_key))
            connection.execute(
                "UPDATE interactions SET state='completed', issue_url=?, issue_number=? WHERE interaction_id=?",
                (result.issue_url, result.issue_number, job.interaction_id))
            connection.execute("COMMIT")
            return result
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ApprovalServiceError("Approval service storage is unavailable; GitHub outcome may be unknown") from None
        finally:
            connection.close()

    def recover_pending(self) -> int:
        """Resume durable approvals accepted before a process restart."""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT interaction_id, discord_user_id, draft_id, target_repository, draft_sha256, "
                "draft_title, draft_body, action FROM interactions "
                "WHERE decision='approve' AND state IN ('accepted', 'failed') ORDER BY created_at"
            ).fetchall()
        finally:
            connection.close()
        resumed = 0
        for row in rows:
            draft = self.drafts.get(row["draft_id"])
            if (draft is None or draft.target_repository != row["target_repository"]
                    or draft.content_sha256 != row["draft_sha256"] or draft.title != row["draft_title"]
                    or draft.body != row["draft_body"] or draft.action != row["action"]):
                continue
            job = ExecutionJob(row["interaction_id"], "", draft, row["discord_user_id"])
            try:
                self.execute(job)
                resumed += 1
            except ApprovalServiceError:
                # Keep a safe failure state; a later startup or human retry can retry it.
                continue
        return resumed


def _update_discord_original(application_id: str, token: str, content: str) -> None:
    """Best-effort ephemeral response update; do not expose interaction token."""
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{urllib.parse.quote(token, safe='')}/messages/@original"
    request = urllib.request.Request(url, data=json.dumps({"content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "CommitmentProof-approval-service"}, method="PATCH")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError, OSError, urllib.error.HTTPError):
        # No token, URL, headers, or response body are logged.
        return


class _Handler(BaseHTTPRequestHandler):
    service: ApprovalService

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/discord/interactions":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > _MAX_BODY_BYTES:
            self.send_error(413)
            return
        raw = self.rfile.read(length)
        response = self.service.handle_request(raw, self.headers)
        encoded = json.dumps(response.payload).encode("utf-8")
        self.send_response(response.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
        self.wfile.flush()
        if response.job:
            try:
                result = self.service.execute(response.job)
                message = f"Follow-up issue created: {result.issue_url}"
            except ApprovalServiceError:
                message = "Issue creation did not complete. Check the approval service status before retrying."
            _update_discord_original(self.service.discord_application_id,
                                     response.job.interaction_token, message)

    def log_message(self, format: str, *args: Any) -> None:
        # Discord interaction token is in request JSON; suppress request logging.
        return


def serve() -> None:
    service = ApprovalService.from_environment()
    port = int(os.environ.get("APPROVAL_SERVICE_PORT", "8765"))
    handler = type("ApprovalHandler", (_Handler,), {"service": service})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    threading.Thread(target=service.recover_pending, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    serve()
