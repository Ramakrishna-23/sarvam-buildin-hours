# web-client

Rider/driver WebRTC clients for Basha Bridge (HLD §4/A, §4/B). One React app, role picked at join time — not two separate codebases, so rider and driver stay in sync as the same build.

Uses LiveKit for the room. Ships with a **dev-only token server** (`server/`) so the two clients can be tested end-to-end right now, without waiting on the orchestrator backend. The real backend should own token issuance once it exists — see the note in `server/tokenServer.mjs`.

## Setup

1. Create a free project at [livekit.io](https://livekit.io) (or use an existing one) — grab the project URL, API key, and API secret.
2. `cp server/.env.example server/.env` and fill in `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`. `CLIENT_ORIGIN` defaults to `http://localhost:5173` (Vite's default) — only update it if Vite picks a different port (e.g. 5173 already in use), since the token server only accepts requests from this exact origin.
3. `cp .env.example .env` (defaults already point at the local token server, no edits needed unless you move it).
4. `npm install`

## Run

Two terminals:

```bash
npm run server   # dev token server on :8787
npm run dev      # vite dev server
```

Open the printed Vite URL in two tabs on the **same machine** (the token server is loopback-only, so a second device on the network can't reach it):

- Tab 1: `?role=rider&room=demo-ride-1`
- Tab 2: `?role=driver&room=demo-ride-1`

Same room name, different role → both join the same LiveKit room, mic audio flows both ways, participant list shows who's talking.

## What's here vs. what's not

- ✅ Room join, mic publish, remote audio playback, participant list with speaking indicator, mute/leave.
- ❌ Agent event handling (`agent_joined` / `your_turn` / etc.) — intentionally left out until the agent architecture (LiveKit Agent Builder vs. custom orchestrator) is confirmed with the rest of the team, since it changes whether the agent shows up as a normal room participant or needs a separate event channel.
- ❌ Targeted-playback UI (visually distinguishing who the agent is currently speaking to) — same reason.
