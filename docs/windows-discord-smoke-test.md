# Windows PowerShell smoke test: Discord approval service

This procedure verifies the local approval flow with ephemeral test keys and mocked GitHub calls. It does not contact Discord or GitHub and cannot create an issue. Do not pass `--apply` to the registration helper during this smoke test.

## Local no-credential smoke test

Open PowerShell and run:

```powershell
Set-Location 'C:\Users\manis\CommitmentProof'
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m unittest tests.test_discord_approval_service tests.test_register_discord_command -v
python -m unittest discover -s tests -v
python register_discord_command.py --application-id 12345678901234567 --guild-id 23456789012345678
git diff --check
```

The registration helper should print `DRY RUN`, the guild-scoped Discord API URL and the manifest payload. It should not make a request. The two test commands create ephemeral Ed25519 keys, exercise signed interactions, and mock issue creation. `.env.example` is documentation only; these tests do not load or require it.

## Manual setup before any live run

These steps require an operator with the Discord application and server and a separately administered trusted service host. They are not performed by the smoke test.

1. In the Discord Developer Portal, open the existing application. On **General Information**, record the Application ID and Public Key, then later set **Interactions Endpoint URL** to `https://<trusted-host>/discord/interactions` after that URL is live and passes Discord's signed PING validation.
2. Obtain the intended server's Guild ID and the approving human's Discord user ID. Populate the corresponding service settings in the trusted host's secret manager. Use `COMMITMENTPROOF_DISCORD_GUILD_ID` only for the registration helper.
3. Prepare a reviewed immutable draft catalog JSON file and a persistent SQLite path on the trusted host. Provision the matching Ed25519 signing key pair and a least-privilege GitHub token through that host's protected secret configuration.
4. Run the service only on a supervised host and account outside agent control. Ensure agents cannot read its environment, process memory, secret store, database, or draft catalog. The registration bot token should be available only to the operator running registration; it is not needed by the service.
5. Configure a public TLS reverse proxy to forward only `POST /discord/interactions` to `127.0.0.1:8765/discord/interactions`. Preserve the raw body and both `X-Signature-Ed25519` and `X-Signature-Timestamp` headers. Disable request-body and interaction-token URL logging. Allow required outbound HTTPS to Discord and GitHub.
6. Authorize/install the new application in the intended server with the `applications.commands` scope. After explicit operator authorization to make that external change, set `COMMITMENTPROOF_DISCORD_APPLICATION_ID` and `COMMITMENTPROOF_DISCORD_GUILD_ID` in the registration operator's secure environment and run `python register_discord_command.py` to review the dry run. Only an authorized operator should set `COMMITMENTPROOF_DISCORD_BOT_TOKEN` securely and add `--apply` to register `/review-followup`.
7. Set the HTTPS Interactions Endpoint URL in the Developer Portal. Discord should validate it using signed PING/PONG. Then verify a non-approving/unauthorized account is rejected and the configured approver can preview the exact draft. Approve only after reviewing the full repository, title and body shown by the service.

The repository does not include deployment automation or a live-service health check. The current local tests and dry run do not verify Discord credentials, server installation, public HTTPS reachability, proxy behavior, or secret isolation on the eventual host.
