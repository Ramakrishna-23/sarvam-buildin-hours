# Deployment Runbook

Goal: let teammates test the Phase 3 full-duplex baseline from their own devices.

Phase 3 needs three running pieces:

1. **Token server** — public HTTPS endpoint that issues LiveKit tokens.
2. **Relay agent worker** — joins the LiveKit room and publishes Hindi + Kannada relay audio tracks.
3. **Web frontend** — static site teammates open on phones/laptops.

LiveKit itself is already cloud-hosted via `LIVEKIT_URL`.

---

## Required secrets

Set these on both the token server and relay agent worker:

```env
SARVAM_API_KEY=...
LIVEKIT_URL=wss://...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
```

Set this on the relay agent worker:

```env
LIVEKIT_ROOM=basha-demo
```

Set this on the frontend build:

```env
VITE_TOKEN_ENDPOINT=https://<token-service-domain>/token
VITE_DEFAULT_ROOM=basha-demo
```

---

## Option A — Fastest real deployment

### 1. Deploy token server as a web service

Use root directory:

```text
basha-bridge
```

Use Dockerfile:

```text
basha-bridge/Dockerfile
```

Start command can be left as the Docker default:

```bash
basha-token-server --host 0.0.0.0
```

The server reads the cloud platform's `PORT` env var automatically.

Health check:

```text
https://<token-service-domain>/healthz
```

Expected:

```json
{"ok": true}
```

### 2. Deploy relay agent as a worker/background service

Use the same Dockerfile/root directory.

Override start command:

```bash
sh -c 'basha-livekit-agent --room "${LIVEKIT_ROOM:-basha-demo}"'
```

Expected logs:

```text
agent joined room 'basha-demo'
published agent track: agent-hi
published agent track: agent-kn
```

The agent needs outbound network access only. It does not expose an HTTP port.

### 3. Deploy frontend as a static site

Use root directory:

```text
basha-bridge/web
```

Build command:

```bash
npm install && npm run build
```

Publish directory:

```text
dist
```

Environment:

```env
VITE_TOKEN_ENDPOINT=https://<token-service-domain>/token
VITE_DEFAULT_ROOM=basha-demo
```

---

## Teammate test links

After frontend deploy, share:

```text
https://<frontend-domain>/?role=driver&room=basha-demo
https://<frontend-domain>/?role=customer&room=basha-demo
```

Phase 3 directions are hardcoded:

```text
driver Kannada → customer Hindi via agent-hi
customer Hindi → driver Kannada via agent-kn
```

Each listener should hear:

- original human audio ducked to a low level
- their target-language relay track at full volume

Use headphones for full-duplex testing to avoid acoustic speaker → mic feedback.

---

## Option B — Immediate tunnel from your laptop

If cloud deployment is slow, you can still let teammates test using tunnels.

Run locally:

```bash
uv run basha-token-server --host 0.0.0.0 --port 8787
cd web && npm run dev -- --host 0.0.0.0
uv run basha-livekit-agent --room basha-demo
```

Expose token server and frontend with a tunnel provider such as ngrok/cloudflared.

Frontend link must use the public token endpoint, either via env or URL param:

```text
https://<frontend-tunnel>/?role=customer&room=basha-demo&tokenEndpoint=https://<token-tunnel>/token
```

---

## Troubleshooting

- If token health fails, check LiveKit env vars on the token server.
- If frontend connects but agent does nothing, confirm relay worker is running for the same `LIVEKIT_ROOM`.
- If agent logs no source track, confirm browser identities are exactly `driver` and `customer`.
- If customer hears only original audio, confirm the agent published `agent-hi` and customer tab attached it.
- If driver hears only original audio, confirm the agent published `agent-kn` and driver tab attached it.
- If audio loops or becomes chaotic, use headphones first; the agent structurally ignores its own LiveKit tracks, but browser speaker audio can still leak acoustically into a human mic.
- Browser mic requires HTTPS except on localhost.
