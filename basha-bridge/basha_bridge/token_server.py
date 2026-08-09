"""Tiny local token service for Phase 2 frontend.

Run:
    uv run python -m basha_bridge.token_server --port 8787

Frontend calls:
    GET /token?room=basha-demo&role=driver
    GET /token?room=basha-demo&role=customer
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from livekit import api

from . import config


class TokenHandler(BaseHTTPRequestHandler):
    server_version = "BashaBridgeToken/0.1"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send_headers(204, "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._json({"ok": True})
            return
        if parsed.path != "/token":
            self._json({"error": "not found"}, status=404)
            return

        params = parse_qs(parsed.query)
        room = params.get("room", ["basha-demo"])[0].strip() or "basha-demo"
        role = params.get("role", ["customer"])[0].strip() or "customer"
        identity = params.get("identity", [role])[0].strip() or role

        if role not in {"driver", "customer", "observer"}:
            self._json({"error": "role must be driver, customer, or observer"}, status=400)
            return
        if not (config.LIVEKIT_URL and config.LIVEKIT_API_KEY and config.LIVEKIT_API_SECRET):
            self._json({"error": "LiveKit env vars are not configured"}, status=500)
            return

        metadata = json.dumps({"role": role})
        token = (
            api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
            .with_identity(identity)
            .with_name(role.title())
            .with_metadata(metadata)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .to_jwt()
        )
        self._json(
            {
                "url": config.LIVEKIT_URL,
                "token": token,
                "room": room,
                "identity": identity,
                "role": role,
            }
        )

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _send_headers(self, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send_headers(status, "application/json")
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(prog="basha-token-server")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8787")))
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), TokenHandler)
    print(f"token server listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
