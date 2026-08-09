# Phase 3 Runbook — Full-Duplex Live Relay

Goal: natural two-way translated conversation with both relays live at the same time.

Phase 3 baseline intentionally does **not** add drift gating, language detection, turn gating, or task mediation. Both relay pipelines run independently.

## 1. Prerequisites

Set `.env` in `basha-bridge/`:

```env
SARVAM_API_KEY=...
LIVEKIT_URL=wss://...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
```

Validate connectivity:

```bash
uv run basha-bridge --check
```

## 2. Start token server

```bash
uv run basha-token-server --host 0.0.0.0 --port 8787
```

This serves LiveKit tokens for the browser clients.

## 3. Start frontend

```bash
cd web
npm install
npm run dev
```

Open the HTTPS Vite URL on two devices/browsers.

Use:

- `?role=driver&room=basha-demo` on the Kannada speaker device
- `?role=customer&room=basha-demo` on the Hindi speaker device

If the token server runs on another machine, set the `Token endpoint` field to:

```text
http://<token-server-host>:8787/token
```

## 4. Start LiveKit relay agent

In another terminal from `basha-bridge/`:

```bash
uv run basha-livekit-agent --room basha-demo
```

Defaults:

```text
driver   kn-IN → customer hi-IN on agent-hi
customer hi-IN → driver   kn-IN on agent-kn
```

Expected startup logs:

```text
agent joined room 'basha-demo' as 'relay-agent'
published agent track: agent-hi
published agent track: agent-kn
```

When both browser clients publish mic tracks, expected source logs include both directions:

```text
subscribed to source track from driver; starting relay driver kn-IN->customer hi-IN -> agent-hi
subscribed to source track from customer; starting relay customer hi-IN->driver kn-IN -> agent-kn
```

## 5. Demo flow

1. Driver and customer join the same room.
2. Both clients publish mic audio.
3. Agent subscribes only to human tracks (`driver`, `customer`).
4. Agent runs two independent pipelines:

```text
driver mic PCM   → Saaras STT kn-IN → Mayura kn→hi → Bulbul hi-IN TTS → agent-hi
customer mic PCM → Saaras STT hi-IN → Mayura hi→kn → Bulbul kn-IN TTS → agent-kn
```

5. Customer hears `agent-hi` at full volume and original driver audio ducked low.
6. Driver hears `agent-kn` at full volume and original customer audio ducked low.

Use headphones for testing. The agent has a structural self-echo guard against subscribing to its own LiveKit tracks, but speaker audio can still leak acoustically into browser microphones.

## 6. Success criteria

- Customer hears Hindi relay while driver speaks Kannada.
- Driver hears Kannada relay while customer speaks Hindi.
- Both directions can overlap; there is no turn gate in Phase 3.
- No LiveKit-track feedback loop: agent does not subscribe to `relay-agent` output tracks.
- Per-direction rolling lag remains roughly within the Phase 2 target, ≤ 2.5 s under good network/audio conditions.

## 7. Troubleshooting

- Browser mic requires HTTPS or localhost. The Vite dev server uses a basic SSL cert; accept the browser warning.
- Customer should attach only `relay-agent / agent-hi`.
- Driver should attach only `relay-agent / agent-kn`.
- If the agent logs no source track, confirm identities are exactly `driver` and `customer`.
- If only one direction works, check the agent logs for both `starting relay ... agent-hi` and `starting relay ... agent-kn`.
- If relay sounds looped or chaotic, test with headphones first and lower device speaker volume.
