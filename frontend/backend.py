"""Same-origin local adapter for the evidence collector and policy gate.

Run from the repository root with ``python frontend/backend.py`` after a
frontend build. GitHub credentials stay in this server process, never in the
browser bundle.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DIST = Path(__file__).resolve().parent / "dist"
sys.path.insert(0, str(ROOT))

from commitment_proof import assess  # noqa: E402
from github_evidence import collect_github_evidence  # noqa: E402

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_REQUEST_BYTES = 8192


def investigate(payload: Any, collector: Callable[..., dict[str, Any]] = collect_github_evidence) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Request must be a JSON object")
    repository = payload.get("repository")
    number = payload.get("pull_request_number")
    if not isinstance(repository, str) or not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("Repository must use owner/name form")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError("Pull request number must be a positive integer")
    evidence = collector(repository, number)
    policy = assess(evidence)
    return {
        "repository": repository,
        "pull_request_number": number,
        "evidence": evidence,
        "assessment": policy.to_dict(),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(DIST), **kwargs)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            self._json(200, {"status": "ok"})
            return
        if self.path.startswith("/api/"):
            self._json(404, {"error": "API route not found"})
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/investigate":
            self._json(404, {"error": "API route not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._json(413, {"error": "Request body is invalid or too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self._json(200, investigate(payload))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "Request JSON is invalid"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception:
            self._json(502, {"error": "Evidence investigation failed; check backend status"})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def serve() -> None:
    if not DIST.is_dir():
        raise SystemExit("Frontend build is missing; run npm run build in frontend first")
    port = int(os.environ.get("FRONTEND_BACKEND_PORT", "8766"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"CommitmentProof UI and local API listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    serve()
