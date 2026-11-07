import contextlib
import io
import json
import unittest
from unittest.mock import Mock

from register_discord_command import main, register_command, request_plan


APPLICATION_ID = "12345678901234567"
GUILD_ID = "23456789012345678"
TOKEN = "mock-discord-token-do-not-print"


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class RegisterDiscordCommandTests(unittest.TestCase):
    def test_dry_run_prints_guild_scoped_plan_without_network(self):
        opener = Mock(side_effect=AssertionError("dry run must not call the network"))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = main(["--application-id", APPLICATION_ID, "--guild-id", GUILD_ID])
        output = stdout.getvalue()
        self.assertEqual(result, 0)
        self.assertIn(f"/applications/{APPLICATION_ID}/guilds/{GUILD_ID}/commands", output)
        self.assertIn('"name":"review-followup"', output)
        opener.assert_not_called()

    def test_apply_posts_only_manifest_command_with_bot_auth(self):
        url, expected_body = request_plan(APPLICATION_ID, GUILD_ID)
        result = {"id": "34567890123456789", "name": "review-followup"}
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["body"] = request.data
            seen["authorization"] = request.get_header("Authorization")
            seen["timeout"] = timeout
            return FakeResponse(result)

        self.assertEqual(register_command(APPLICATION_ID, GUILD_ID, TOKEN, opener=opener), result)
        self.assertEqual(seen["url"], url)
        self.assertEqual(seen["body"], expected_body)
        self.assertEqual(seen["authorization"], f"Bot {TOKEN}")
        self.assertEqual(seen["timeout"], 15)

    def test_invalid_ids_fail_before_network(self):
        with self.assertRaises(ValueError):
            request_plan("not-an-id", GUILD_ID)

    def test_failure_does_not_include_bot_token(self):
        def opener(_request, **_kwargs):
            raise OSError(f"failed with {TOKEN}")

        with self.assertRaises(RuntimeError) as raised:
            register_command(APPLICATION_ID, GUILD_ID, TOKEN, opener=opener)
        self.assertNotIn(TOKEN, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
