# Phase 4 Runbook — Language Detect, Pair Lock, Relay Gate

Goal: cold-start session with no assumed languages. The agent listens silently, locks each side's dominant language, then either starts relay for a mismatch or stays silent for same-language calls.

Phase 4 baseline intentionally does **not** add drift gating or mediation. Language mismatch alone opens the relay; same language keeps the agent silent.

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

## 3. Start frontend

```bash
cd web
npm install
npm run dev
```

Open the HTTPS Vite URL on two devices/browsers.

Use:

- `?role=driver&room=basha-demo`
- `?role=customer&room=basha-demo`

## 4. Start LiveKit relay agent

```bash
uv run basha-livekit-agent --room basha-demo
```

Expected startup logs:

```text
agent joined room 'basha-demo' as 'relay-agent'
published agent track: agent-to-customer
published agent track: agent-to-driver
```

## 5. Language lock flow

1. Both humans join and publish microphone audio.
2. Agent starts silent and detects first utterances per role using:

```text
buffered LiveKit PCM → WAV → Saaras REST STT language_code="unknown" → text identify_language
```

3. Each role locks after 2 consistent detections.
4. If both roles lock to the same language, the agent publishes a `relay_gate` event with `mode="silent"` and never emits relay audio.
5. If roles lock to different languages, the agent publishes `mode="relay"`, ducks original human audio in the browser, and starts two streaming relay pipelines.

For the common demo pair, expected logs look like:

```text
language locked role=driver language=kn-IN
language locked role=customer language=hi-IN
relay gate resolved: mismatch {'driver': 'kn-IN', 'customer': 'hi-IN'}; starting [...]
subscribed to source track from driver; starting relay driver kn-IN->customer hi-IN -> agent-to-customer
subscribed to source track from customer; starting relay customer hi-IN->driver kn-IN -> agent-to-driver
```

## 6. Success criteria

- `hi-IN + kn-IN` speakers: pair locks and relay begins within about 2 exchanges.
- `hi-IN + hi-IN` speakers: pair locks as same-language and the agent stays silent.
- Dominant language does not flip because of one code-mixed/borrowed word; each side needs 2 consistent detections.
- Self-echo guard remains structural: the agent subscribes only to human `driver` / `customer` tracks.

## 7. Browser behavior

- Before lock: original human audio is full volume; agent tracks are silent.
- Mismatch relay: original human audio ducks low; role-target agent track is full volume.
- Same language: original human audio remains full volume; agent stays silent.

Role-target tracks:

```text
customer hears agent-to-customer
driver hears agent-to-driver
```

## 8. Troubleshooting

- Speak two short, clear utterances on each side to lock language.
- If the gate does not resolve, check Railway logs for `language sample role=...`.
- If language detection fails repeatedly, check Sarvam credentials and that microphone audio is not too quiet.
- If the same-language test is too quiet, confirm browser event log says `same-language call detected`; human audio should be volume 1.0 in that mode.
- If relay sounds looped or chaotic, use headphones first. The agent ignores its own LiveKit tracks, but speaker audio can still leak acoustically into a human mic.
