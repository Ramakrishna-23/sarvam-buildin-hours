# Basha Bridge

Full-duplex realtime hi ⇄ kn voice interpretation over LiveKit WebRTC, powered
end-to-end by Sarvam (`saaras:v3-realtime` → `mayura:v1` → `bulbul:v3`).

Not turn-based: translation is committed **clause-by-clause while the person is
still speaking** (~1.2–2 s ear-to-ear), like a simultaneous interpreter.

- Architecture diagram: open `docs/streaming-architecture.html` in a browser
- Design doc: `docs/streaming-translation-architecture.md`

## Setup

```bash
cp .env.example .env   # fill SARVAM_API_KEY, LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
uv sync
uv run basha-bridge check
```

## Offline pipeline test (no WebRTC, uses fixtures)

```bash
uv run python -m basha_bridge.offline_pipeline fixtures/kn_pickup.wav --tgt hi-IN
uv run python -m basha_bridge.offline_pipeline fixtures/hi_otp.wav   --tgt kn-IN
```

Streams the fixture at real-time pace through realtime STT → chunker → Mayura →
Bulbul, prints partials/commits/latency table, writes `out.wav`.

## Live demo

```bash
# terminal 1 — the agent joins the room and interprets both directions
uv run basha-bridge agent --room demo

# terminal 2 — build frontend once, then serve
cd frontend && npm install && npm run build && cd ..
uv run basha-bridge serve --port 8080
```

Open on two devices (mic permission needed; use ngrok/tailscale for a phone):

- `http://localhost:8080/?room=demo&id=rider`   → speak Hindi
- `http://localhost:8080/?room=demo&id=driver`  → speak Kannada
- `http://localhost:8080/debug`                 → pipeline debugger (fixtures + live bus)

For frontend hot-reload during development: `cd frontend && npm run dev` (proxies `/api` to :8080).

First utterance from each side auto-locks the language pair (badge turns
green), then both directions interpret continuously. Live captions, translated
segments, and an ear-to-ear latency ticker stream over the room data channel.

Tuning env vars: `BB_LOCK_FINALS` (finals needed to lock a language, default 1),
`BB_LOCK_MIN_CONF` (default 0.5), `BB_MEDIATION=0` to disable the drift engine
and run as a pure interpreter.

## Drift engine & mediation (Phase 5/6)

The agent is silent by default. It watches for *drift* — confusion, repeated
questions, stalled task slots, frustration — and climbs an escalation ladder:

```
PASSIVE_MONITOR → WATCH → OFFER_HELP → ACTIVE_MEDIATION → RESOLVED
ANY_STATE → SAFETY_ESCALATION
```

Different languages alone is **not** drift (weighted below the WATCH
threshold): it takes a comprehension or task-progress failure to escalate.

Once mediating, the agent pauses both translation pipelines, takes the floor,
and asks one targeted question at a time until the pickup slots
(`pickup_point · driver_location · eta · agreed_next_action`) are filled, then
gives a bilingual summary and steps back.

Replay scenarios offline — no audio, no WebRTC:

```bash
uv run basha-bridge replay scenarios/*.json              # rules only, deterministic
uv run basha-bridge replay scenarios/pickup_fail.json --llm
uv run basha-bridge replay scenarios/mediation_happy_path.json --llm --mediate
```

Scenarios carry assertions (`expect.final_state`, `expect.reaches`,
`expect.never`) and the harness exits non-zero on a mismatch. They are also
runnable from the dashboard's **Drift scenarios** panel, which visualizes the
ladder, drift score, signals, task slots, and agent actions live.

`--llm` adds one `sarvam-105b` call per turn (frustration, contradiction,
safety, slot extraction) via forced tool-calling; without it the engine runs on
rules alone, which is what the unit tests exercise.

## Tests

```bash
uv run pytest -q   # chunker + drift/ladder unit tests, no network
```
