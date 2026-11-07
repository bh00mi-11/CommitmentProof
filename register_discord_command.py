"""Safely register the single CommitmentProof command in one Discord guild.

The default invocation only prints a request plan. Pass --apply explicitly to
send the single-command POST. Credentials are read from the environment and
never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

API_BASE = "https://discord.com/api/v10"
SNOWFLAKE_RE = re.compile(r"^[0-9]{17,20}$")


def request_plan(application_id: str, guild_id: str) -> tuple[str, bytes]:
    if not SNOWFLAKE_RE.fullmatch(application_id) or not SNOWFLAKE_RE.fullmatch(guild_id):
        raise ValueError("Application ID and guild ID must be Discord snowflakes")
    manifest_path = Path(__file__).with_name("discord_commands.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, list) or len(manifest) != 1
            or not isinstance(manifest[0], dict)
            or manifest[0].get("name") != "review-followup"):
        raise ValueError("Command manifest must define only review-followup")
    url = f"{API_BASE}/applications/{application_id}/guilds/{guild_id}/commands"
    body = json.dumps(manifest[0], separators=(",", ":")).encode("utf-8")
    return url, body


def register_command(
    application_id: str,
    guild_id: str,
    bot_token: str,
    *,
    opener: Callable = urllib.request.urlopen,
) -> dict:
    if not bot_token:
        raise ValueError("DISCORD_BOT_TOKEN is required with --apply")
    url, body = request_plan(application_id, guild_id)
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
            "User-Agent": "CommitmentProof-command-registration",
        },
        method="POST",
    )
    try:
        with opener(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("Discord command registration failed; response details suppressed") from None
    if not isinstance(result, dict) or result.get("name") != "review-followup":
        raise RuntimeError("Discord returned an unexpected command registration response")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-id", default=os.environ.get("DISCORD_APPLICATION_ID"))
    parser.add_argument("--guild-id", default=os.environ.get("DISCORD_GUILD_ID"))
    parser.add_argument("--apply", action="store_true", help="perform the Discord API write")
    args = parser.parse_args(argv)
    if not args.application_id or not args.guild_id:
        parser.error("set DISCORD_APPLICATION_ID and DISCORD_GUILD_ID or pass the corresponding flags")
    try:
        url, body = request_plan(args.application_id, args.guild_id)
        if not args.apply:
            print("DRY RUN — no Discord request sent")
            print(f"POST {url}")
            print(body.decode("utf-8"))
            print("To register explicitly, set DISCORD_BOT_TOKEN securely and rerun with --apply.")
            return 0
        result = register_command(args.application_id, args.guild_id,
                                  os.environ.get("DISCORD_BOT_TOKEN", ""))
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Registered guild command /{result['name']} (command ID {result.get('id', 'unknown')}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
