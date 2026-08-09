# Sarvam Voice Stack — Reference for Basha Bridge

Researched from https://docs.sarvam.ai (Aug 2026). This is the grounding doc for the
STT / LLM / TTS choices in [HLD.md](./HLD.md).

---

## 1. Two ways to consume Sarvam

| | **Model APIs** (`api.sarvam.ai`) | **Voice Agents** (formerly Samvaad) |
|---|---|---|
| What | Raw STT / TTS / LLM / translate endpoints | Managed end-to-end ASR→LLM→TTS agent runtime |
| Control | Full — you own the orchestrator | Opinionated; you configure an agent, not a loop |
| Channels | Whatever you build (WebRTC, etc.) | Telephony (in/out), WhatsApp, web widget, API/SDK |
| Custom logic | Yes | Limited — no BYO models ("does not support bringing external models into the stack") |
| Docs | `/api/...` | `/conversations/...` |

**Decision for us:** Model APIs, not Voice Agents. Voice Agents assumes
one-user ↔ one-agent and owns the turn loop. Our mediator is silent-observer +
two-party + custom escalation policy, which the managed runtime cannot express.
We borrow its *patterns* (VAD-driven barge-in, fast stream type), not its runtime.

Worth noting for the pitch: every model in the Voice Agents stack is **self-hosted by
Sarvam in India** (no third-party hops, PII stays in-country). Same models back the
raw APIs.

---

## 2. Model inventory (current, Aug 2026)

| Model | ID | Modality | Languages | Notes |
|---|---|---|---|---|
| **Saaras v3** | `saaras:v3` | STT | 23 (22 Indic + En) | 5 modes; auto lang detect; 8kHz telephony tuned |
| **Bulbul v3** | `bulbul:v3` | TTS | 11 (10 Indic + En) | 30+ voices; pace 0.5–2.0x; up to 48kHz (REST only) |
| **Mayura** | `mayura` | Translate | 11 | Context-preserving; style + script control |
| **Sarvam-Translate** | `sarvam-translate` | Translate | 23 | Wider language coverage |
| **Sarvam-105B** | `sarvam-105b` | LLM | 11 | 128K context, reasoning + agentic |
| **Sarvam-105B Conversations** | `sarvam-105b-conversations` | LLM | 11 | **Tuned for real-time dialogue / voice agents** |
| **Sarvam-30B** | `sarvam-30b` | LLM | 11 | 64K context, cheaper/faster |
| Sarvam Vision | `sarvam-vision` | DocAI | 23 | Not relevant to us |
| GLM-5.2 / Gemma-4-31B | `glm-5.2`, `gemma-4-31b` | LLM (beta) | not Indic-tuned | Skip |

Deprecated / avoid: `saarika:v2.5` (STT), `sarvam-m`, `bulbul:v2`,
and the `/speech-to-text-translate` endpoint (folded into `/speech-to-text` with
`mode="translate"`).

---

## 3. STT — Saaras v3

### Modes (the `mode` param, single `/speech-to-text` endpoint)

| Mode | Output |
|---|---|
| `transcribe` (default) | Native script, formatted, numbers normalized |
| `translate` | Indic speech → English text directly (one hop, no separate translate call) |
| `verbatim` | Word-for-word, keeps fillers + spoken numbers |
| `translit` | Romanized Latin script |
| `codemix` | English words in Latin, Indic words in native script |

For the mediator: **`transcribe` for the transcript** (native script feeds TTS cleanly),
and consider a parallel `translate` pass only if the drift-engine LLM prompt is easier
in English. `verbatim` is useful if drift detection wants disfluency/repetition signals
— fillers and repeated words are exactly the confusion evidence the drift engine keys on.

### Three transports

| API | Endpoint | Limit | Use |
|---|---|---|---|
| REST | `POST /speech-to-text` | 30 s per request | Quick / fallback |
| Batch | async job | 2 h per file, 20 files/job | Diarization + timestamps, post-call analytics |
| **Realtime** | `wss://api.sarvam.ai/speech-to-text-realtime/ws` | streaming | **Our primary path** |

### Realtime WS specifics (model `saaras:v3-realtime`)

- **Live partial transcripts** — v3-realtime emits interim results as the user speaks.
  The older streaming API only gave finals per utterance. This is what makes early
  barge-in and early drift signals possible.
- **`language_code`** required; 24 accepted values including **`auto`** for adaptive
  detection. Matches our "do not pre-assume language" decision.
- **`stream_type`**: `fast` | `balanced` | `simulated`.
  Docs explicitly say *"Use `stream_type="fast"` for conversational agents."*
  `simulated` = finals only (cheapest, use for the passive-monitor path if `fast`
  proves too chatty).
- **VAD**, millisecond-tunable: `threshold`, `silence_duration_ms`,
  `min_speech_duration_ms`. Server-side auto VAD or manual client control.
  Emits speech start/end events → drive turn attribution + barge-in from these,
  not from final transcripts.
- **`config.update` mid-call** — change settings without reconnecting. Useful when we
  flip PASSIVE_MONITOR → ACTIVE_MEDIATION and want to switch stream_type or VAD
  aggressiveness on the same socket.
- **Audio**: base64 PCM — `linear16`, `linear32`, `mulaw`, `alaw`. **8000 or 16000 Hz only.**
  LiveKit gives us 48kHz Opus → we must downsample to 16k PCM in the orchestrator.
- Entity preservation for proper nouns; speaker diarization available via **batch only**
  (not realtime) — so speaker attribution in our two-party room must come from
  per-participant tracks, which our LiveKit topology already gives us. Good.

---

## 4. TTS — Bulbul v3

| Transport | Endpoint | Char limit | Output |
|---|---|---|---|
| REST | `POST https://api.sarvam.ai/text-to-speech` | 2500 | base64 WAV in JSON (`audios[]`) |
| HTTP stream | `POST /text-to-speech/stream` | 3500 | raw binary, starts on first chunk |
| **WebSocket** | `/text-to-speech/ws` | 2500/msg, **<500 recommended** | base64 chunks |

**Latency: sub-250 ms first-byte via WebSocket streaming** (Sarvam's Bulbul v3 claim).
That's the number to design the budget around. WS also keeps the connection warm, so
successive utterances have lower TTFB than a cold HTTP call.

Params: `model=bulbul:v3`, `speaker` (30+, **lowercase, case-sensitive** — `shubh`
default male, `ishita` female/conversational, plus aditya, ritu, priya, neha, roopa…),
`target_language_code` (BCP-47), `pace` 0.5–2.0, `speech_sample_rate`,
`output_audio_codec` (wav, mp3, linear16, mulaw, alaw, opus, flac, aac).

Gotchas that matter for us:
- **Sample rates >24 kHz are REST-only.** Streaming caps at 24 kHz. Fine — we're
  playing into a call, 24 kHz is plenty.
- **Never send romanized Indic text to TTS.** Quality degrades badly. Our
  translate step must output `fully-native` script. Already in the HLD; this confirms it.
- **No true server-side cancel on the TTS stream.** Barge-in = stop playback locally in
  the browser and drop/reopen the socket. Budget for reconnect cost — keep a warm
  spare socket per participant if reopen latency bites.
- Voice quality varies per language; `shubh`/`ishita` are not the best pick in every
  language. Pick per-language speakers for Hindi and Kannada before the demo.

---

## 5. LLM

Use **`sarvam-105b-conversations`** for the mediator turn — it's the variant
post-trained for real-time dialogue, so it should beat plain `sarvam-105b` on
latency and on producing short, speakable utterances.

- Strict **JSON schema structured outputs** are supported → the drift classifier and
  the bounded action enum (`ASK_RIDER_LANDMARK`, `CONFIRM_WITH_DRIVER`, …) are
  enforceable at the API layer, not by prompt hope.
- 128K context — we will never approach it. Keep the window small on purpose
  (last N turns + task slots) for latency.
- Consider **`sarvam-30b`** (64K) for the per-turn drift classifier: it fires on every
  turn, so it's the hot path, while `105b-conversations` fires only during active
  mediation. Two-tier routing mirrors what Sarvam's own Voice Agents stack does
  ("routed automatically based on the kind of request").

---

## 6. Latency budget (target for the demo)

| Stage | Budget | Source |
|---|---|---|
| LiveKit audio → orchestrator | ~50–80 ms | WebRTC typical |
| STT partial (stream_type=fast) | ~150–300 ms after speech | v3-realtime partials |
| VAD end-of-speech → final | `silence_duration_ms` (tunable, ~300–500 ms) | our config |
| Drift classify (sarvam-30b, short ctx) | ~200–400 ms | est. |
| Mediator turn (105b-conversations, JSON) | ~400–800 ms | est. |
| Translate (Mayura) — if used | ~150–300 ms | est. |
| **TTS first byte (Bulbul v3 WS)** | **<250 ms** | Sarvam published |
| Playback to browser | ~50 ms | local |

**Rough end-to-end for an agent utterance: ~1.2–2.0 s.** Two levers if it's too slow:
(a) skip the separate Mayura call and have the LLM emit the target-language text
directly, (b) start TTS streaming on the first sentence of the LLM output rather than
waiting for the full JSON. (b) conflicts with strict JSON output — so prefer emitting
the utterance as a plain-text field the moment the schema's `utterance` key streams in,
or do a two-call split: fast plan JSON, then fast utterance text.

Latency rule already in the HLD holds: **keep agent utterances short**, and don't run
the big LLM on every turn.

---

## 7. Pricing (₹, per Sarvam pricing page)

| Service | Price |
|---|---|
| STT (Saaras) | ₹30–45 / hour of audio (higher with diarization + translation) |
| TTS Bulbul v3 | ₹30 / 10,000 chars (v2: ₹15) |
| Translate / transliterate (Mayura) | ₹20 / 10,000 chars |
| Language ID | ₹3.5 / 10K chars |
| Sarvam-105B | ₹29.28 / M input tokens (+ separate cached-input & output rates) |
| GLM-5.2 | ₹128.1 / M input tokens |
| Doc digitization | ₹0.5 / page |

New accounts get **₹100 free credits**. For a hackathon demo that's roughly ~2 hours of
STT plus a few hundred TTS utterances — enough, but not for careless load testing.
Two concurrent STT streams (rider + driver) run continuously in passive monitor mode,
so **STT is the dominant cost line**, ~₹60–90/hour of session. If credits get tight,
gate the passive-monitor STT with client-side VAD so we only stream during speech.

### Rate limits
Per **account**, not per key. Token-bucket (continuous replenish, no hard reset).
Starter 60 req/min → Pro 200 → Business 1000. WebSocket **concurrent connection**
limits are separate from req/min — check the dashboard before the demo, since we open
2 STT + 2 TTS sockets per session and a Starter cap could bite with multiple test runs.
Handle `429` and `503` with exponential backoff.

---

## 8. Confirmed mapping to our HLD

```text
Saaras v3 (saaras:v3-realtime, stream_type=fast, language_code=auto)
    → transcript + language detection + VAD/turn events
Sarvam-30B (strict JSON)          → per-turn drift classification  [hot path]
Sarvam-105B-Conversations (JSON)  → mediation action + utterance   [only when active]
Mayura (modern-colloquial, fully-native script) → target-language text
Bulbul v3 (WS, per-language speaker) → targeted audio, <250 ms TTFB
```

Deltas from the original HLD worth acting on:
1. HLD says model `Saaras v3` — the realtime variant ID is **`saaras:v3-realtime`** and
   it needs `stream_type` + VAD params set explicitly.
2. HLD says `Sarvam-105B` for all reasoning — **split it**: 30B for the always-on drift
   classifier, 105B-conversations for mediation turns.
3. Add a **48kHz → 16kHz PCM downsample** step between LiveKit and STT. Not in the HLD.
4. Barge-in mitigation ("stop playback locally, reopen stream") is confirmed correct —
   there is genuinely no server-side TTS cancel.

## 9. Open items to verify against live API

- Actual measured TTFB for `saaras:v3-realtime` partials at `stream_type=fast`
  (docs give no number; only Bulbul's <250 ms is published).
- Whether `sarvam-105b-conversations` supports the same strict JSON schema as
  `sarvam-105b` — the models page doesn't say, and our whole determinism story
  depends on it. **Test this first.**
- WebSocket concurrency cap on our plan tier.
- Per-language best speaker for Hindi and Kannada.

---

Sources: [Models](https://docs.sarvam.ai/api/getting-started/models) ·
[Saaras v3](https://docs.sarvam.ai/api/getting-started/models/saaras) ·
[STT overview](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/overview) ·
[STT realtime](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-api) ·
[TTS overview](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/overview) ·
[TTS streaming](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/streaming-api) ·
[Bulbul model](https://docs.sarvam.ai/api-reference-docs/models/bulbul) ·
[Voice Agents overview](https://docs.sarvam.ai/conversations/overview) ·
[Voice Agents models](https://docs.sarvam.ai/conversations/build/models) ·
[Pricing](https://docs.sarvam.ai/api/getting-started/pricing) ·
[Rate limits](https://docs.sarvam.ai/api/getting-started/ratelimits) ·
[Changelog](https://docs.sarvam.ai/api/getting-started/changelog)
