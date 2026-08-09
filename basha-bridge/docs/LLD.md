# LLD: Voice Intermediary Task Mediator (Basha Bridge)

Companion to [HLD.md](./HLD.md) and [sarvam-voice-stack.md](./sarvam-voice-stack.md).
This document pins down schemas, state machines, scoring logic, API sequences, and
fallbacks so implementation can proceed without further design decisions.

> Field shapes for Sarvam WS messages follow the docs as researched Aug 2026 — verify
> exact key names against the live API reference before coding the clients
> (marked ⚠️ where unverified).

---

## 1. System components (code-level)

```text
basha-bridge/
├── web/            React + Vite + livekit-client   (rider & driver join same app)
└── server/         Python 3.11 + FastAPI
    ├── session/    session state, Redis-free in-memory store (MVP)
    ├── media/      LiveKit subscriber, resampler (48k→16k PCM)
    ├── stt/        Saaras v3 realtime WS client (one per participant)
    ├── drift/      rule engine + LLM classifier
    ├── mediator/   turn manager + task state + Sarvam-105B-conversations calls
    ├── speak/      translate (Mayura) + TTS (Bulbul v3 WS) + audio routing
    └── events/     browser control-channel WS (one per participant)
```

One orchestrator process per session (MVP: single process, sessions in a dict).

---

## 2. Session schema

```jsonc
{
  "session_id": "s_8f2c",              // short uuid
  "created_at": "2026-08-09T10:00:00Z",
  "livekit_room": "bb-s_8f2c",
  "state": "PASSIVE_MONITOR",          // §7 state machine
  "drift": {
    "score": 0.0,
    "last_decay_turn": 0,
    "hard_trigger": null,              // null | "manual" | "safety" | "repeated_failure"
    "history": [ {"turn": 12, "signal": "lang_mismatch_stable", "delta": 2.0} ]
  },
  "participants": {
    "rider":  { /* Participant */ },
    "driver": { /* Participant */ }
  },
  "transcript": [ /* TranscriptTurn, append-only */ ],
  "task": { /* TaskState, §6 */ },
  "turn_manager": { /* §8 */ },
  "metrics": { "stt_ms": [], "llm_ms": [], "tts_ttfb_ms": [] }
}
```

### Participant

```jsonc
{
  "role": "rider",                     // "rider" | "driver"
  "identity": "lk-identity-string",    // LiveKit participant identity
  "control_ws": "<conn ref>",          // browser control channel
  "stt_ws": "<conn ref>",              // dedicated Saaras stream
  "tts_ws": "<conn ref|null>",         // opened lazily on first agent utterance
  "lang_profile": {
    "detected": [ {"lang": "hi-IN", "count": 7, "avg_conf": 0.91} ],
    "primary": "hi-IN",                // null until stable (≥2 finals, conf ≥ 0.7)
    "stable": true
  },
  "speaking": false,                   // from VAD events
  "last_final_at": "…"
}
```

**Language stability rule:** `primary` is set when the same language tops the last 2
final transcripts with average confidence ≥ 0.7. Until both participants are stable,
the `lang_mismatch` drift signal is suppressed.

---

## 3. Transcript format

```jsonc
// TranscriptTurn — one per STT *final*; partials update a scratch buffer, not this list
{
  "turn_id": 14,                       // monotonically increasing per session
  "speaker": "driver",
  "text": "ನಾನು ಗೇಟ್ ಒಂದರ ಹತ್ತಿರ ಇದ್ದೇನೆ",   // native script (mode=transcribe)
  "text_en": "I am near gate one",     // filled lazily when drift/mediator needs it
  "lang": "kn-IN",
  "confidence": 0.88,
  "is_question": false,                // heuristic + classifier
  "ts_start": 132.4,                   // seconds from session start
  "ts_end": 135.1,
  "during_mediation": false
}
```

- Partials live in `participant.partial_buffer` and are pushed to browsers via
  `transcript.partial` events; only finals enter the transcript.
- `text_en` is produced by a single batched Mayura/`mode=translate` call over the
  last-N window when the drift LLM or mediator runs — not per turn (cost + latency).

---

## 4. Browser ⇄ backend WebSocket event schema

One control WS per browser: `wss://<server>/ws/{session_id}/{role}`.
All messages: `{ "type": string, "ts": number, ...payload }`.

### Client → server

| type | payload | notes |
|---|---|---|
| `join` | `{role}` | after LiveKit room join succeeds |
| `audio.chunk` | `{data_b64, seq}` | **fallback path only** (16k PCM, 100 ms frames) |
| `help.request` | `{}` | manual help button → hard trigger |
| `barge_in` | `{}` | user started talking while agent audio playing |
| `tts.playback_done` | `{utterance_id}` | closes the agent-speaking window |
| `leave` | `{}` | |

### Server → client

| type | payload | notes |
|---|---|---|
| `session.state` | `{state, drift_score}` | on every state change |
| `transcript.partial` | `{speaker, text, lang}` | live UI |
| `transcript.final` | `TranscriptTurn` | |
| `agent.joined` | `{reason}` | fires on → ACTIVE_MEDIATION |
| `agent.utterance` | `{utterance_id, target, text_native, lang}` | subtitle for the audio |
| `agent.audio` | `{utterance_id, seq, data_b64, codec:"mp3", final:bool}` | only sent to `target` |
| `turn.grant` | `{speaker, prompt_hint}` | UI highlights the speaker |
| `turn.wait` | `{speaker}` | UI dims the other side |
| `mediation.resolved` | `{summary_en, summary_native:{rider,driver}}` | |
| `error` | `{code, message, recoverable}` | |

**Targeted playback rule:** `agent.audio` for an utterance is sent **only** on the
target participant's control WS. The other participant simultaneously gets
`turn.wait` + the subtitle via `agent.utterance` (so the demo audience sees both sides).

---

## 5. Sarvam API contracts

Auth for all: header `api-subscription-key: $SARVAM_API_KEY`.

### 5.1 STT — `saaras:v3-realtime`

```text
wss://api.sarvam.ai/speech-to-text-realtime/ws
  ?language_code=auto
  &model=saaras:v3-realtime
```

Config on connect (⚠️ verify keys):

```jsonc
{
  "type": "config",
  "stream_type": "fast",               // partials for barge-in + early drift
  "mode": "transcribe",
  "input_audio": {"encoding": "linear16", "sample_rate": 16000},
  "vad": {"threshold": 0.5, "silence_duration_ms": 400, "min_speech_duration_ms": 120}
}
```

Then `{"type":"audio","data":"<b64 pcm>"}` every 100 ms.
Receive: `speech.start` / `speech.end` (VAD), `transcript.partial`,
`transcript.final {text, language_code, confidence}`.

On PASSIVE→ACTIVE transition send `config.update` (same socket, no reconnect):
tighter `silence_duration_ms: 300` for snappier mediation turns.

### 5.2 Drift classifier — `sarvam-30b`

`POST https://api.sarvam.ai/v1/chat/completions`, strict JSON schema
(`response_format: json_schema`), `max_tokens ≤ 150`, temperature 0.

```jsonc
// response schema (enforced)
{
  "comprehension_failure": bool,
  "repeated_question": bool,
  "contradiction": bool,
  "frustration": bool,
  "task_progressing": bool,
  "safety_concern": bool,
  "confidence": 0.0
}
```

Input: last 8 turns (`text_en`), current task slots, both language profiles.
Called when rule-score crosses WATCH, then every 3rd turn while ≥ WATCH.

### 5.3 Mediator — `sarvam-105b-conversations`

Same endpoint, strict JSON schema (⚠️ **verify the conversations variant accepts
`json_schema` — open item #2 in sarvam-voice-stack.md; fallback = `sarvam-105b`**):

```jsonc
{
  "action": "ASK_RIDER_PICKUP_POINT | ASK_RIDER_LANDMARK | ASK_DRIVER_LOCATION |
             ASK_DRIVER_FEASIBILITY | ASK_DRIVER_ETA | CONFIRM_WITH_RIDER |
             CONFIRM_WITH_DRIVER | SUMMARIZE_AND_EXIT | SAFETY_ESCALATE",
  "target": "rider | driver | both",
  "utterance_en": "string, ≤ 25 words",
  "slots_update": { "pickup_point": "…", "eta": "…" },   // only changed slots
  "task_complete": false,
  "reasoning": "one line, for logs only"
}
```

**The app validates `action` against the turn manager's allowed set** (§8) and
re-prompts once on violation; second violation → deterministic fallback action.

### 5.4 Translate — Mayura

`POST https://api.sarvam.ai/translate` —
`{input, source_language_code:"en-IN", target_language_code, mode:"modern-colloquial",
output_script:"fully-native"}`. Skipped when the mediator can be prompted to emit
target-language text directly (latency lever; try in M5).

### 5.5 TTS — `bulbul:v3` WebSocket

```text
wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v3
```

Config → text → flush; keep sockets warm per participant after first use.

```jsonc
{"type":"config","speaker":"<per-lang table>","target_language_code":"hi-IN",
 "pace":1.0,"output_audio_codec":"mp3","speech_sample_rate":24000}
{"type":"text","text":"<native script only>"}
{"type":"flush"}
```

Utterances ≤ 500 chars (latency sweet spot). **Never send romanized Indic.**
Speaker table (fill after listening tests): `hi-IN: …`, `kn-IN: …`, `en-IN: …`.

**Barge-in:** no server-side cancel. On `barge_in` event: browser stops playback
immediately; server marks utterance abandoned, drops + reopens that TTS socket.

---

## 6. Task slot schema (ride-pickup task)

```jsonc
{
  "task_type": "ride_pickup",
  "slots": {
    "pickup_point":       {"value": null, "source_turn": null, "confirmed_by": []},
    "landmark":           {"value": null, "source_turn": null, "confirmed_by": []},
    "driver_location":    {"value": null, "source_turn": null, "confirmed_by": []},
    "rider_location":     {"value": null, "source_turn": null, "confirmed_by": []},
    "eta":                {"value": null, "source_turn": null, "confirmed_by": []},
    "otp":                {"value": null, "source_turn": null, "confirmed_by": [], "protected": true},
    "blocker":            {"value": null, "source_turn": null, "confirmed_by": []},
    "agreed_next_action": {"value": null, "source_turn": null, "confirmed_by": []}
  },
  "required_for_resolution": ["pickup_point", "eta", "agreed_next_action"],
  "resolution_rule": "all required slots filled AND each confirmed_by includes both roles"
}
```

**Entity protection:** slots flagged `protected` (OTP; extend to gate numbers,
vehicle no., amounts) are extracted by regex/verbatim from the source transcript turn
and are **never rewritten by translation or the LLM** — the mediator utterance
templates splice the protected raw string in unchanged.

---

## 7. Drift engine

### 7.1 Rule layer (runs on every final transcript)

| # | Signal | Condition | Δscore |
|---|---|---|---|
| S1 | `lang_mismatch_stable` | both profiles stable & different | +2.0 (once) |
| S2 | `low_stt_confidence` | final confidence < 0.6 | +1.0 |
| S3 | `explicit_confusion` | phrase table hit ("samajh nahi", "ಅರ್ಥ ಆಗ್ತಿಲ್ಲ", "what?", …) | +3.0 |
| S4 | `repeated_question` | fuzzy sim > 0.8 vs same speaker's prior question | +2.0 |
| S5 | `slots_stalled` | ≥ 6 turns & no required slot filled | +2.0 |
| S6 | `contradiction` | slot value conflicts with earlier confirmed value | +2.0 |
| S7 | `silence_after_question` | > 8 s after a question final | +1.0 |
| S8 | `frustration` | keyword table hit | +2.0 |
| — | decay | per clean turn (no signal fired) | −1.0, floor 0 |

Hard triggers (bypass score): `help.request` → OFFER_HELP immediately;
safety keyword table → SAFETY_ESCALATION; 3 consecutive S3/S4 turns → ACTIVE_MEDIATION.

### 7.2 Thresholds & confirmation

```text
score ≥ 3  → WATCH        (start LLM classifier polling, every 3rd turn)
score ≥ 6  → OFFER_HELP   only if classifier confirms (comprehension_failure
                           OR !task_progressing) with confidence ≥ 0.6
score ≥ 8  → ACTIVE_MEDIATION (classifier confirmation not required)
```

The LLM classifier can also **veto**: if it returns `task_progressing: true` with
confidence ≥ 0.8, subtract 2.0 (humans muddling through fine → don't butt in).

### 7.3 OFFER_HELP behavior

Short agent utterance to **both** sides in their languages:
"I can help coordinate if needed — say 'help' or tap the button."
If either accepts (verbal "help"-intent or button) → ACTIVE_MEDIATION.
If ignored for 4 turns and score < 6 → back to WATCH.

---

## 8. Turn manager (ACTIVE_MEDIATION only)

```text
        ┌─────────────┐  grant(X)   ┌──────────────┐  final from X  ┌────────────┐
  ──►   │ AGENT_SPEAK │ ──────────► │ WAIT_FOR(X)  │ ─────────────► │ PROCESSING │
        └─────▲───────┘             └──────┬───────┘                └─────┬──────┘
              │        timeout 12s: re-ask │ (once) then ask other side   │
              └───────────── next utterance ◄─────────────────────────────┘
                              (mediator LLM picks action from ALLOWED set)
```

- **Allowed-action set** is computed by the app from task state, e.g. if
  `pickup_point` empty → `{ASK_RIDER_PICKUP_POINT, ASK_RIDER_LANDMARK, SAFETY_ESCALATE}`.
  The LLM chooses *within* this set; the app enforces it (§5.3).
- While `WAIT_FOR(rider)`: rider gets `turn.grant`, driver gets `turn.wait`.
  Finals from the non-granted speaker are transcribed but **not** fed to the mediator
  (logged, shown in transcript UI).
- `AGENT_SPEAK` ends on `tts.playback_done` or `barge_in` (barge-in from the granted
  speaker is treated as an early answer).
- Exit: mediator returns `task_complete: true` **and** app verifies
  `resolution_rule` → `SUMMARIZE_AND_EXIT` → RESOLVED → PASSIVE_MONITOR
  (drift score reset to 0, S1 suppressed for 10 turns).

---

## 9. End-to-end sequences

### 9.1 Passive turn (hot path, no LLM)

```text
LiveKit track frame (48k Opus)
  → media.resampler (16k PCM mono, 100ms frames)
  → stt_ws[speaker].send(audio)
  ← partial → browsers (transcript.partial)
  ← final   → transcript.append → drift.rules(turn)
              → score < 3 ? done : classifier path (§7.2)
```

### 9.2 Mediation utterance

```text
turn_manager.next()
  → allowed_actions = f(task_state)
  → 105b-conversations (strict JSON, last-8 en-window + slots)     ~400–800 ms
  → validate action ∈ allowed
  → utterance_en → Mayura (fully-native, target lang)              ~150–300 ms
  → splice protected entities verbatim
  → tts_ws[target] config→text→flush                               <250 ms TTFB
  → agent.audio chunks → target browser only
  → agent.utterance subtitle → both browsers
  → on playback_done → WAIT_FOR(target)
```

Budget ≈ 1.2–2.0 s speech-to-speech. Levers if over: LLM emits target-language text
directly (drop Mayura hop); stream TTS from first sentence.

---

## 10. Fallback ladder

| Trigger | Fallback |
|---|---|
| LiveKit server-side subscribe unstable | **F1:** browsers capture mic → `audio.chunk` over control WS (LiveKit call itself stays up for human audio) |
| Continuous streaming still flaky | **F2:** push-to-talk for mediation answers only (turn.grant arms the mic button) |
| STT realtime WS quota/latency | **F3:** 6-s chunked REST `/speech-to-text` during passive mode; realtime only in ACTIVE |
| TTS WS issues | **F4:** REST TTS, play whole clip (accept ~1 s extra) |
| `105b-conversations` no strict JSON | **F5:** `sarvam-105b` with schema; or conversations + regex-extract + one retry |

All fallbacks are config flags (`FALLBACK_AUDIO=ws_chunks`, etc.) — switchable
mid-demo without redeploy.

---

## 11. Demo script flow (target: 3 minutes)

```text
0:00  Both browsers join room "bb-demo". Status: PASSIVE_MONITOR (badge visible).
0:10  Rider (Hindi): "Bhaiya, main Gate 2 pe hoon, chai ki dukaan ke paas."
0:20  Driver (Kannada): "ಗೊತ್ತಾಗ್ತಿಲ್ಲ, ಯಾವ ಗೇಟ್?"  → S1 armed, S3 fires. Score 5, WATCH.
0:35  Rider repeats question (S4). Score 7 → classifier confirms → OFFER_HELP.
0:45  Agent (both langs): "I can help coordinate…" Rider says "haan help karo".
0:50  ACTIVE_MEDIATION. Agent → rider (Hindi): "Pickup gate aur ek landmark boliye."
1:05  Rider: "Gate 2, chai ki dukaan ke paas."  → slots: pickup_point, landmark.
1:15  Agent → driver (Kannada): "ರೈಡರ್ ಗೇಟ್ 2 ಬಳಿ… ತಲುಪಬಹುದಾ?" 
1:30  Driver: "ಆಯ್ತು, ಐದು ನಿಮಿಷ."  → eta filled, feasibility confirmed.
1:40  Agent confirms with rider (Hindi), rider: "theek hai" → agreed_next_action.
1:50  SUMMARIZE_AND_EXIT in both languages → RESOLVED banner → PASSIVE_MONITOR.
2:00  (Buffer for latency / one barge-in showcase: rider interrupts agent mid-TTS.)
```

Scripted lines are pinned in `demo/script.md`; both demo phones/laptops get printed
cue cards.

---

## 12. Config & constants (single source: `server/config.py`)

```text
STT_SAMPLE_RATE=16000        FRAME_MS=100
VAD_SILENCE_MS=400 (passive) / 300 (active)
DRIFT_WATCH=3  DRIFT_OFFER=6  DRIFT_ACTIVE=8  DECAY=1.0
CLASSIFIER_EVERY_N=3         CLASSIFIER_MODEL=sarvam-30b
MEDIATOR_MODEL=sarvam-105b-conversations   MEDIATOR_MAX_WORDS=25
TURN_TIMEOUT_S=12            REASK_LIMIT=1
TTS_MODEL=bulbul:v3          TTS_MAX_CHARS=500   TTS_CODEC=mp3  TTS_SR=24000
LANG_STABLE_MIN_FINALS=2     LANG_STABLE_MIN_CONF=0.7
```
