"""Demo web server: serves the React dashboard and mints LiveKit tokens.

    uv run basha-bridge serve --port 8080

Open http://localhost:8080/debug for the pipeline debugger, or
http://localhost:8080/?room=demo&id=rider for the live demo.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError
from livekit import api

from . import config
from .offline_pipeline import FIXTURE_META, stream_run

ROOT = Path(__file__).parent.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"
FIXTURES_DIR = ROOT / "fixtures"


async def token_handler(request: web.Request) -> web.Response:
    room = request.query.get("room", "demo")
    identity = request.query.get("identity", "guest")
    token = (
        api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .to_jwt()
    )
    return web.json_response({"url": config.LIVEKIT_URL, "token": token})


async def fixtures_handler(_request: web.Request) -> web.Response:
    items = []
    for name, meta in FIXTURE_META.items():
        path = FIXTURES_DIR / name
        items.append(
            {
                "name": name,
                "exists": path.exists(),
                "lang": meta["lang"],
                "speaker": meta["speaker"],
                "text": meta["text"],
                "notes": meta["notes"],
                "default_tgt": "kn-IN" if meta["lang"] == "hi-IN" else "hi-IN",
            }
        )
    return web.json_response(items)


async def offline_run_handler(request: web.Request) -> web.StreamResponse:
    fixture = request.query.get("fixture", "hi_otp.wav")
    tgt = request.query.get("tgt", "kn-IN")
    src = request.query.get("src", "auto")
    path = FIXTURES_DIR / fixture
    if not path.exists():
        return web.json_response({"error": f"fixture not found: {fixture}"}, status=404)

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)

    out_path = ROOT / ".context" / "offline-out.wav"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        async for evt in stream_run(path, src, tgt, out_path):
            if request.transport is not None and request.transport.is_closing():
                break
            payload = json.dumps(evt, ensure_ascii=False)
            try:
                await resp.write(f"data: {payload}\n\n".encode())
            except (ClientConnectionResetError, ConnectionResetError, BrokenPipeError):
                break
    except (ClientConnectionResetError, ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    except Exception as exc:
        try:
            err = json.dumps({"tag": "run.error", "message": str(exc)}, ensure_ascii=False)
            await resp.write(f"data: {err}\n\n".encode())
        except (ClientConnectionResetError, ConnectionResetError, BrokenPipeError):
            pass

    return resp


async def spa_handler(request: web.Request) -> web.FileResponse | web.Response:
    """Serve built React assets; fall back to index.html for client routes."""
    rel = request.match_info.get("path", "")
    if rel:
        candidate = FRONTEND_DIST / rel
        if candidate.is_file():
            return web.FileResponse(candidate)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return web.FileResponse(index)
    return web.Response(
        text="Frontend not built. Run: cd frontend && npm install && npm run build",
        status=503,
        content_type="text/plain",
    )


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/token", token_handler)
    app.router.add_get("/api/fixtures", fixtures_handler)
    app.router.add_get("/api/offline-run", offline_run_handler)
    app.router.add_get("/", spa_handler)
    app.router.add_get("/{path:.*}", spa_handler)
    return app


def run_server(port: int = 8080) -> None:
    web.run_app(build_app(), port=port)
