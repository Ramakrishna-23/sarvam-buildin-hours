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
from .replay import stream_replay

ROOT = Path(__file__).parent.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"
FIXTURES_DIR = ROOT / "fixtures"
SCENARIOS_DIR = ROOT / "scenarios"


async def _sse(request: web.Request, source) -> web.StreamResponse:
    """Stream an async generator of dicts as server-sent events."""
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)
    try:
        async for evt in source:
            if request.transport is not None and request.transport.is_closing():
                break
            payload = json.dumps(evt, ensure_ascii=False)
            try:
                await resp.write(f"data: {payload}\n\n".encode())
            except (ClientConnectionResetError, ConnectionResetError, BrokenPipeError):
                break
    except (
        ClientConnectionResetError,
        ConnectionResetError,
        BrokenPipeError,
        asyncio.CancelledError,
    ):
        pass
    except Exception as exc:
        try:
            err = json.dumps({"tag": "run.error", "message": str(exc)}, ensure_ascii=False)
            await resp.write(f"data: {err}\n\n".encode())
        except (ClientConnectionResetError, ConnectionResetError, BrokenPipeError):
            pass
    return resp


async def scenarios_handler(_request: web.Request) -> web.Response:
    items = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        items.append(
            {
                "file": path.name,
                "name": data.get("name", path.stem),
                "description": data.get("description", ""),
                "turns": len(data.get("turns", [])),
                "expect": data.get("expect", {}),
            }
        )
    return web.json_response(items)


async def scenario_run_handler(request: web.Request) -> web.StreamResponse:
    name = request.query.get("scenario", "pickup_fail.json")
    use_llm = request.query.get("llm", "0") not in ("0", "false", "")
    mediate = request.query.get("mediate", "0") not in ("0", "false", "")
    path = SCENARIOS_DIR / Path(name).name
    if not path.exists():
        return web.json_response({"error": f"scenario not found: {name}"}, status=404)
    return await _sse(request, stream_replay(path, use_llm, mediate))


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
    app.router.add_get("/api/scenarios", scenarios_handler)
    app.router.add_get("/api/scenario-run", scenario_run_handler)
    app.router.add_get("/", spa_handler)
    app.router.add_get("/{path:.*}", spa_handler)
    return app


def run_server(port: int = 8080) -> None:
    web.run_app(build_app(), port=port)
