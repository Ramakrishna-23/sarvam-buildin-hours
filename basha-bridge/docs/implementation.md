# Implementation Guide: Basha Bridge

How to build what [LLD.md](./LLD.md) specifies. Hackathon-scoped: milestones are
ordered so that every checkpoint is independently demoable.

---

## 1. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React 18 + Vite + TypeScript | fast to scaffold, two-role single app |
| WebRTC | LiveKit Cloud (free tier) + `livekit-client` | rooms, per-participant tracks, tokens |
| Backend | Python 3.11 + FastAPI + uvicorn | async WS everywhere; `livekit` (rtc) SDK mature in Python |
| Audio | `livekit.rtc` AudioStream + `soxr`/`numpy` resample | 48k float → 16k int16 PCM |
| Sarvam | raw `websockets` + `httpx` (skip the SDK for WS paths) | SDK may lag realtime API; REST via SDK is fine |
| State | in-process dict (MVP) | one server, ≤ a few sessions |
| Deploy | local laptop for demo; Railway as backup remote | zero-ops |

**Env vars** (`server/.env`):

```text
SARVAM_API_KEY=
LIVEKIT_URL=wss://<project>.livekit.cloud
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
FALLBACK_AUDIO=livekit          # livekit | ws_chunks | ptt
MEDIATOR_MODEL=sarvam-105b-conversations
```

---

## 2. Repo layout

```text
basha-bridge/
├── docs/                      # HLD.md, LLD.md, this file, sarvam-voice-stack.md
├── demo/script.md             # pinned demo lines + cue cards
├── web/
│   ├── src/
│   │   ├── App.tsx            # /join/:sessionId/:role
│   │   ├── livekit.ts         # room join, mic publish
│   │   ├── controlWs.ts       # §4 LLD event client
│   │   ├── audioPlayer.ts     # agent MP3 chunk player + barge-in stop
│   │   └── ui/                # StatusBadge, TranscriptPane, TurnHighlight, HelpButton
│   └── …vite scaffolding
└── server/
    ├── main.py                # FastAPI app: /token, /ws/{session}/{role}, /session
    ├── config.py              # LLD §12 constants
    ├── session/store.py       # Session dataclasses (LLD §2)
    ├── media/livekit_sub.py   # hidden participant, per-track AudioStream
    ├── media/resample.py      # 48k→16k mono int16, 100ms framer
    ├── stt/saaras_ws.py       # realtime client, partial/final/VAD callbacks
    ├── drift/rules.py         # LLD §7.1 signals + scoring
    ├── drift/classifier.py    # sarvam-30b strict-JSON call
    ├── mediator/turns.py      # LLD §8 turn manager
    ├── mediator/llm.py        # 105b-conversations call + action validation
    ├── mediator/task.py       # slot store + protected-entity extraction (LLD §6)
    ├── speak/translate.py     # Mayura wrapper
    ├── speak/bulbul_ws.py     # TTS WS client, warm-socket pool
    └── tests/                 # replay-based unit tests (see §6)
```

---

## 3. Milestones (hackathon plan, ~2 devs)

| M | Deliverable | Demoable checkpoint | Est |
|---|---|---|---|
| **M0** | Scaffold: LiveKit room, two browsers talking; FastAPI up; control WS echo | live 2-party call | 2 h |
| **M1** | Server joins room hidden, resamples, streams to 2× Saaras WS; partials/finals in both UIs | **live bilingual transcript** | 3 h |
| **M2** | Language profiles + drift rule engine + score badge in UI | score visibly rises on scripted confusion | 2 h |
| **M3** | TTS path: canned OFFER_HELP utterance, translated, played targeted | **agent speaks to one side only** | 2 h |
| **M4** | Mediator loop: 105b-conversations JSON, turn manager, slots, resolution | **full scripted demo passes** | 4 h |
| **M5** | Polish: barge-in, latency levers, classifier veto, RESOLVED banner | robustness | 3 h |
| **M6** | Fallback flags wired + rehearsals + cue cards | demo insurance | 2 h |

**First 30 minutes of M1: run the two spike tests** (open items from
sarvam-voice-stack.md): (a) `saaras:v3-realtime` handshake + measure partial TTFB,
(b) `sarvam-105b-conversations` with `response_format: json_schema`. If (b) fails,
set `MEDIATOR_MODEL=sarvam-105b` and move on.

---

## 4. Key code sketches

### 4.1 Resampler + framer (`media/resample.py`)

```python
import numpy as np, soxr

class Framer:
    """48k float32 LiveKit frames → 16k int16 mono, fixed 100ms (1600-sample) frames."""
    def __init__(self):
        self.buf = np.empty(0, dtype=np.int16)

    def push(self, pcm48: np.ndarray, channels: int) -> list[bytes]:
        mono = pcm48.reshape(-1, channels).mean(axis=1) if channels > 1 else pcm48
        pcm16 = soxr.resample(mono, 48000, 16000)
        ints = np.clip(pcm16 * 32767, -32768, 32767).astype(np.int16)
        self.buf = np.concatenate([self.buf, ints])
        out = []
        while len(self.buf) >= 1600:
            out.append(self.buf[:1600].tobytes()); self.buf = self.buf[1600:]
        return out
```

### 4.2 Saaras realtime client (`stt/saaras_ws.py`)

```python
class SaarasStream:
    URL = "wss://api.sarvam.ai/speech-to-text-realtime/ws?language_code=auto&model=saaras:v3-realtime"

    def __init__(self, on_partial, on_final, on_vad):
        self.cbs = (on_partial, on_final, on_vad)

    async def run(self, key: str):
        async with websockets.connect(self.URL, extra_headers={"api-subscription-key": key}) as ws:
            self.ws = ws
            await ws.send(json.dumps({
                "type": "config", "stream_type": "fast", "mode": "transcribe",
                "input_audio": {"encoding": "linear16", "sample_rate": 16000},
                "vad": {"silence_duration_ms": 400, "min_speech_duration_ms": 120},
            }))  # ⚠️ verify exact keys vs API reference
            async for raw in ws:
                self._dispatch(json.loads(raw))

    async def send_audio(self, frame: bytes):
        await self.ws.send(json.dumps({"type": "audio",
                                       "data": base64.b64encode(frame).decode()}))

    async def update(self, **cfg):        # PASSIVE→ACTIVE retune, same socket
        await self.ws.send(json.dumps({"type": "config.update", **cfg}))
```

### 4.3 Drift rules core (`drift/rules.py`)

```python
def score_turn(sess, turn) -> list[Signal]:
    fired = []
    p = sess.participants
    if (p["rider"].stable and p["driver"].stable
            and p["rider"].primary != p["driver"].primary
            and not sess.drift.s1_fired):
        fired.append(Signal("lang_mismatch_stable", 2.0))
    if turn.confidence < 0.6:
        fired.append(Signal("low_stt_confidence", 1.0))
    if hits_phrase_table(turn.text, CONFUSION[turn.lang]):
        fired.append(Signal("explicit_confusion", 3.0))
    if turn.is_question and fuzzy_sim(turn, last_question_by(sess, turn.speaker)) > 0.8:
        fired.append(Signal("repeated_question", 2.0))
    # … S5–S8 per LLD §7.1
    if not fired:
        sess.drift.score = max(0.0, sess.drift.score - 1.0)
    else:
        sess.drift.score += sum(s.delta for s in fired)
    return fired
```

Phrase tables are plain YAML per language — 10–15 entries each is enough for the demo;
native script, checked against transcripts during rehearsal.

### 4.4 Mediator call with action enforcement (`mediator/llm.py`)

```python
async def next_move(sess) -> Move:
    allowed = sess.turn_manager.allowed_actions()          # from task state
    schema = mediator_schema(allowed)                      # enum narrowed to allowed
    for attempt in range(2):
        r = await chat(MEDIATOR_MODEL,
                       messages=build_prompt(sess, allowed),
                       response_format={"type": "json_schema", "json_schema": schema},
                       temperature=0.2, max_tokens=200)
        move = Move.parse(r)
        if move.action in allowed:
            return move
    return deterministic_fallback(allowed)                 # scripted question, never stuck
```

Narrowing the schema enum to the allowed set means even a misbehaving model can't
pick an illegal action — validation belt *and* schema suspenders.

### 4.5 Bulbul warm-socket pool (`speak/bulbul_ws.py`)

```python
class TtsPool:
    """One warm WS per (participant, language). Reopen on barge-in abandon."""
    async def speak(self, role, lang, text_native, on_chunk):
        ws = await self._get_or_open(role, lang)          # config sent on open
        await ws.send(json.dumps({"type": "text", "text": text_native}))
        await ws.send(json.dumps({"type": "flush"}))
        # chunks forwarded to on_chunk → control WS agent.audio events

    async def abandon(self, role, lang):                  # barge-in: no server cancel
        await self._close(role, lang)                     # next speak() reopens
```

### 4.6 Frontend audio player (`web/src/audioPlayer.ts`)

MP3 chunks → `MediaSource`/`SourceBuffer` append; `stop()` on `barge_in` detaches the
buffer instantly (this is the local half of barge-in). Mic VAD in the browser
(`hark` or simple RMS threshold) fires `barge_in` when the user speaks during
`agent.audio` playback.

---

## 5. Prompts (versioned in `server/prompts/`)

**Classifier (30b)** — system: "You judge whether a two-person service conversation is
progressing. Answer only the JSON schema. Do not infer problems that aren't evidenced."
User: last-8 en turns + slot table + language profiles.

**Mediator (105b-conversations)** — system: "You are a calm pickup coordinator. One
short question or confirmation at a time, ≤ 25 words, no explanations. Choose only
from ALLOWED_ACTIONS. Never alter OTPs, gate numbers, vehicle numbers." User: task
state + last-8 window + last agent move + ALLOWED_ACTIONS.

Both prompts frozen after M4; changes only via rehearsal failures.

---

## 6. Testing strategy

- **Replay tests (core):** record real STT final sequences (JSON lines) during M1;
  drift rules + turn manager + task store are pure functions over them →
  `tests/replays/*.jsonl` asserted end-to-end without audio or network.
- **Contract spikes:** `tests/spikes/` — the two M1 spike scripts, kept runnable to
  re-verify API shapes after any Sarvam changelog bump.
- **Latency harness:** every Sarvam call wrapped in a timer → `session.metrics`;
  `/session/{id}/metrics` endpoint dumps p50/p95 — checked at each rehearsal.
- **Chaos toggles:** env flags to fake STT WS drop and TTS failure → verify fallback
  ladder F1–F5 actually engages.

---

## 7. Demo runbook

**Setup (T−30 min):** wired network or phone hotspot (venue Wi-Fi is the #1 risk);
two laptops + earphones (prevents echo/cross-talk between demo devices);
`FALLBACK_AUDIO=livekit`; open metrics page on a third screen; pre-create session
`bb-demo`; run one full scripted pass.

**Roles:** Dev A drives rider laptop (Hindi lines), Dev B drives driver laptop
(Kannada lines) — cue cards from `demo/script.md`, timeline in LLD §11.

**Mid-demo failure playbook:**

| Symptom | Action (≤ 10 s) |
|---|---|
| No partials in UI | flip `FALLBACK_AUDIO=ws_chunks`, refresh browsers (F1) |
| STT laggy/choppy | F3 flag: chunked REST passive mode |
| Agent audio silent | F4 flag: REST TTS; narrate "slightly higher latency mode" |
| Mediator JSON garbage | already auto-falls back to deterministic scripted questions |
| Everything on fire | F2 push-to-talk: still shows mediation logic + translation |

**The line that sells it:** point at the badge — "the agent heard everything and said
nothing until the conversation actually broke."

---

## 8. Definition of done (MVP)

- [ ] Two browsers, live WebRTC call, no push-to-talk
- [ ] Live bilingual transcript with partials, per-speaker language badges
- [ ] Agent silent through a working conversation (no false-positive in rehearsal ×3)
- [ ] Scripted confusion → OFFER_HELP → ACTIVE_MEDIATION with targeted audio
- [ ] Slots fill → bilingual summary → RESOLVED → back to silent
- [ ] Barge-in stops agent audio < 300 ms
- [ ] p95 speech-to-speech ≤ 2.5 s on demo hardware
- [ ] All five fallback flags tested once
