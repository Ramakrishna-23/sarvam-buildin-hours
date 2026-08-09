# Phase 2 Runbook — One-Direction Live Relay

Goal: speak Kannada as `driver`, hear rolling Hindi relay as `customer` while the driver is still speaking.

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
- `?role=customer&room=basha-demo` on the Hindi listener device

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
source_identity = driver
source_language = kn-IN
target_language = hi-IN
agent_track_name = agent-hi
```

## 5. Demo flow

1. Driver and customer join the same room.
2. Driver speaks Kannada continuously.
3. Agent subscribes to the driver audio track.
4. Agent runs:

```text
LiveKit track PCM → Saaras realtime STT → Segmenter → Mayura translate → Bulbul TTS → LiveKit agent audio track
```

5. Customer hears original driver audio ducked to ~20% plus Hindi agent relay at full volume.

## 6. Success criteria

- Customer hears Hindi relay before the driver finishes a long sentence.
- Rolling lag does not grow with utterance length.
- Only one direction is supported in Phase 2: Kannada driver → Hindi customer.

## 7. Troubleshooting

- Browser mic requires HTTPS or localhost. The Vite dev server uses a basic SSL cert; accept the browser warning.
- The customer should attach the `agent-hi` audio track. Driver ignores the agent relay track in this phase.
- If the agent logs no source track, confirm the driver identity is exactly `driver`.
- If there is no TTS output, first run Phase 1 offline artifact to verify Sarvam credentials and pipeline health.
