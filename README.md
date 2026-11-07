# CommitmentProof — Prove it before you close it

**Flip the Switch Hackathon · Track 01: Best Business Workflow**

> A merged pull request is not the same as a customer promise kept.

Support asks whether a promised checkout fix is complete. An evidence agent checks linked PR, tests, deployment and QA evidence; a coordination agent prepares the safe next step; a human reviews it. **Switch should be the shared workspace that connects these participants.**

## Current build status

| Component | Status |
| --- | --- |
| Python evidence gate | Implemented, 7 local tests passing |
| Read-only GitHub PR/check-run collector | Implemented in `github_evidence.py`; requires current API access |
| Negative / missing evidence blocks completion | Implemented |
| Human review requirement | Implemented as gate; no external approval integration |
| Switch configuration | Starter template, **not verified against Switch Console** |
| Live Switch messages / agent handoffs | **Not implemented** |
| Authenticated GitHub PR and check-run reads | Implemented as read-only REST API collection; deployment reads remain **not implemented** |
| Signed approval verification for follow-up issues | Implemented locally; requires trusted Discord signer configuration |
| Discord interaction receiver and GitHub execution service | Implemented locally; not deployed |

**Do not present this starter as a deployed Switch integration or live proof.**

## Files

- `commitment_proof.py` — standard-library deterministic evidence checker; not an LLM, GitHub connector or Switch agent.
- `github_evidence.py` — standard-library, read-only GitHub REST API collector for PR merge state and associated check runs. Deployment and QA remain unknown.
- `github_issue_approval.py` — GitHub issue writer gated by a short-lived Ed25519-signed approval proof.
- `discord_approval_service.py` — signed Discord interaction receiver, immutable draft lookup, and SQLite-serialized issue execution.
- `discord_commands.json` — slash-command registration payload for `/review-followup`.
- `tests/test_commitment_proof.py` — seven policy tests.
- `tests/test_github_evidence.py` — mocked HTTP tests for collection, pagination and failure cases.
- `tests/test_github_issue_approval.py` — mocked issue API tests, including forged and stale approval rejection.
- `tests/test_discord_approval_service.py` — mocked signed-interaction, denial, replay, retry, and concurrent execution tests.
- `examples/merged_failed.json` — fictional case showing an unsafe completion claim.
- `switch/commitment-proof.template.yaml` — proposed two-agent room template with placeholders, not validated against the installed Switch version.

## Run

Python 3.10+; the evidence gate and collector use only the standard library. The approval verifier and service require `cryptography` for Ed25519.

```bash
python -m unittest discover -s tests -v
python commitment_proof.py examples/merged_failed.json
python github_evidence.py owner/repository 123
```

Set `GITHUB_TOKEN` in the environment to authenticate (recommended for private repositories and higher API limits). The collector sends it only in the API request header and never prints it. The GitHub account needs read access to the repository and pull requests/checks. The result includes source URLs for the PR and check summary; deployment and QA values remain unknown and therefore cannot satisfy the evidence gate.

## Discord approval trust boundary

Switch messages received by an agent identify the Switch sender; they do not provide an independently verified Discord user ID or an authenticated approval event. A message body, LLM statement, or hand-written approval object is therefore never sufficient. `create_followup_issue` accepts only a signed `DiscordApprovalProof`, verifies Ed25519 against `COMMITMENTPROOF_APPROVAL_PUBLIC_KEY`, and matches the signed Discord ID against `COMMITMENTPROOF_APPROVER_DISCORD_ID`. Configure `GITHUB_TOKEN` separately for the GitHub REST API writer.

The local approval service acts as the Discord approval bridge: it receives Discord's authenticated interaction delivery, verifies Discord's request signature, takes the stable user ID from that verified event, resolves a preloaded immutable draft, and signs a canonical compact sorted-key JSON claim set with an Ed25519 key kept outside every agent process. Claims are `issuer`, `discord_user_id`, `action` (`create_github_issue`), `draft_id`, `draft_sha256` (SHA-256 of normalized title + newline + body), `target_repository`, `approved`, `idempotency_key`, `nonce`, `issued_at`, and `expires_at`. The issue verifier accepts proofs valid for at most ten minutes and requires the configured allowlisted user ID. The review button expires after five minutes and its one-time nonce is stored in SQLite. Agents receive no signing key or GitHub token and cannot create a valid proof themselves.

The repository implements the Discord interaction receiver, proof signer/verifier, SQLite-backed interaction and execution ledger, and cross-thread/process serialization when instances share the same local database. It does **not** register the Discord slash command, provision keys, or deploy the service. The writer must run as a trusted backend outside model-controlled execution, with the Discord public key, signing key, approver ID, and GitHub token injected by protected deployment configuration; do not expose credentials or let an agent choose trust anchors. Until that service is deployed and configured, issue creation should remain disabled. Sequential retries use the signed idempotency key marker in the GitHub issue body; concurrent requests still require serialization because GitHub's create-issue API has no idempotency-key parameter.

### Local Discord service

`discord_approval_service.py` now implements the receiver and local execution path. It verifies Discord's Ed25519 signature over the timestamp and raw request body, rejects stale requests and non-allowlisted Discord user IDs, and accepts only the `review-followup` command with a draft ID. It displays the complete draft and offers explicit ephemeral Approve/Deny buttons. The click interaction is separately signature-verified and its button ID binds the immutable draft digest, repository, and action. Drafts are read once from a JSON catalog with this shape: `{"drafts":[{"draft_id":"draft-42","target_repository":"OWNER/REPO","title":"...","body":"..."}]}`. There is no draft mutation endpoint. The signed approval proof binds the exact normalized title/body digest, repository, draft ID, action, user, expiry, nonce, and idempotency key.

Install `cryptography` with `python -m pip install -r requirements.txt`. Register `discord_commands.json` by POSTing it to `https://discord.com/api/v10/applications/{application_id}/commands` with the Discord bot token in the authorization header. Command registration is a separate external setup step and is not performed by this repository. Run with `python discord_approval_service.py` after setting `DISCORD_PUBLIC_KEY` (Discord application's hex public key), `DISCORD_APPLICATION_ID`, `COMMITMENTPROOF_APPROVER_DISCORD_ID`, `COMMITMENTPROOF_APPROVAL_SIGNING_PRIVATE_KEY` (base64 raw Ed25519 private key), its matching `COMMITMENTPROOF_APPROVAL_PUBLIC_KEY` (base64 raw public key), `GITHUB_TOKEN`, `APPROVAL_DRAFTS_PATH`, and `APPROVAL_DB_PATH`. The signing key, Discord bot token, and GitHub token must be provided only to the trusted service host, never to Codex or other agents. The service binds to `127.0.0.1` by default; configure Discord's interaction endpoint through a TLS reverse proxy that exposes only `/discord/interactions`. The endpoint is authenticated by Discord's request signature; unrelated paths are rejected.

SQLite stores interaction IDs, expiring one-use preview nonces, immutable draft snapshots, and execution outcomes. Execution claims are committed before GitHub I/O and use a bounded 450-second lease, so concurrent requests and restarts do not hold a database write lock across network calls. The stable issue-body marker lets a retry recover after an uncertain GitHub response. Accepted approvals are resumed after a process restart. The endpoint responds to component interactions with a deferred-update callback before GitHub work; the HTTP handler then best-effort updates the ephemeral response. Discord requires an initial interaction response within three seconds, so request-side storage waits are capped at one second and outbound GitHub work occurs only after the acknowledgment. Configure the app for HTTP outgoing webhooks (not simultaneous Gateway interaction delivery). The database must be on a local persistent filesystem. Protect it with host ACLs and backups. Production still requires registering the slash command and interaction URL, securely provisioning secrets, supervised service hosting, TLS/reverse-proxy configuration, and operational recovery/monitoring. Do not run the service in the agent's workspace or expose its secrets to an agent process.

The sample must return `BLOCKED` and `can_claim_complete: false`.

## Minimum actual Switch acceptance

1. Install and configure Switch Console using the [official resources](https://www.wemakedevs.org/hackathons/switch/resources).
2. Start one real room with human support manager and two functioning agents; adapt the template to the installed version.
3. Evidence agent retrieves **real, current, linked** GitHub PR/check/deployment records with read-only access; never fabricate sources.
4. Evidence agent hands findings to coordinator inside Switch and coordinator requests human review.
5. Failed tests, unknown deployment and contradictory QA must keep a promise open.
6. On **explicit human authorization**, create an actual follow-up task once; verify the GitHub result and record it in the room.
7. Record a reproducible live Switch demo and preserve anonymized logs. No automatic customer communication.

**Safety:** The checker always leaves `can_claim_complete` false. The evidence collector is read-only. The issue module can write to GitHub only when called with a valid signed approval proof and configured credentials; no customer communication is implemented.

Official challenge: https://www.wemakedevs.org/hackathons/switch
