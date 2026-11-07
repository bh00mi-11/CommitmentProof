"""JSON HTTP adapter shared by Vercel's file-based Python functions."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from typing import Any

from frontend.backend import MAX_REQUEST_BYTES, api_response


class VercelApiHandler(BaseHTTPRequestHandler):
    """Expose the existing read-only investigation API as a Vercel function."""

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 405:
            self.send_header("Allow", "POST")
        self.end_headers()
        self.wfile.write(raw)

    def _dispatch(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "Content-Length is invalid"})
            return
        if length < 0:
            self._json(400, {"error": "Content-Length is invalid"})
            return
        if length > MAX_REQUEST_BYTES:
            self._json(413, {"error": "Request body is invalid or too large"})
            return
        body = self.rfile.read(length) if length else b""
        status, payload = api_response(self.command, self.path, body)
        self._json(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch()

    def log_message(self, _format: str, *_args: Any) -> None:
        return
