# Streaming Translation Architecture (Full-Duplex, Non-Turn-Based)

Goal: continuous hi↔kn simultaneous interpretation with ~1.2–2 s ear-to-ear lag,
instead of the half-duplex turn pipeline (5–7 s after end of utterance).

Every API event/tag below is verified against the installed `sarvamai` SDK
(`.venv/.../sarvamai`, spec `>=0.1`) — not from docs or memory.

---

## 0. Key discovery: `saaras:v3-realtime`

The SDK ships a **realtime STT endpoint** the current plan doesn't use:
`client.speech_to_text_realtime_streaming.connect(...)` → model
**`saaras:v3-realtime`**, which emits **`transcript.partial` events during the
utterance**. This is the primitive that makes non-turn-based translation
possible. The older `speech_to_text_streaming` (plan Phase 1) only returns
whole-utterance results after END_SPEECH — keep it as fallback only.

Connect parameters (verified signature):

```python
client.speech_to_text_realtime_streaming.connect(
    model="saaras:v3-realtime",
    language_code="auto",          # or hi-IN / kn-IN once pair-locked
    stream_type="fast",            # fast = partials at low latency | balanced | simulated (no partials)
    mode="transcribe",             # applied to FINAL only; partials are always raw transcription
    endpointing="vad",             # or "manual" (we drive speech_start/speech_end ourselves)
    encoding="linear16",           # pcm_s16le
    sample_rate="16000",
    return_timestamps="true",
    threshold=...,                 # VAD activation
    prefix_padding_ms=...,
    silence_duration_ms="350",     # end-of-utterance silence — the finalization latency knob
    min_speech_duration_ms=...,
)
```

### Server → client events (STT)

| `event` tag | Fields | Use |
|---|---|---|
| `session.begin` | resolved config echo (`turn_detection`, `silence_duration_ms`, …) | sanity-check config |
| `vad.speech_start` | `utterance_idx`, `confidence` | barge-in trigger, silence timers, UI "speaking" |
| `transcript.partial` | `utterance_idx`, `text` (cumulative so far), `language` (only when `auto`) | **feeds the chunker** |
| `transcript.final` | `utterance_idx`, `text`, `language`, `language_confidence`, `start_s`, `end_s` | truth for transcript log, drift engine, pair lock |
| `vad.speech_end` | `utterance_idx`, `confidence` | flush chunker remainder |
| `config.updated` | applied keys | confirm mid-stream reconfig |
| `error` | code (`invalid_config`, …) | recovery |
| `pong` / `session.end` | — | keepalive / teardown |

### Client → server messages (STT)

| `event` tag | Payload | Use |
|---|---|---|
| `audio_input` | `audio`: base64 pcm_s16le | 100 ms frames from LiveKit |
| `speech_start` / `speech_end` | — | only under `endpointing=manual` |
| `flush` | — | force finalization now |
| `config.update` | any of `language_code`, `mode`, `prompt`, `stream_type`, `endpointing` (boundary-gated); `threshold`, `silence_duration_ms`, `min_speech_duration_ms` (immediate) | **pair lock without socket restart** |
| `end` / `ping` | — | teardown / keepalive |

Facts that shape the design:

- **Partials are cumulative** per utterance ("text so far"). The chunker diffs
  successive partials of the same `utterance_idx`.
- **Partials are always raw transcription in the sticky language**; `mode` and
  `language` detection resolve on the final. (STT `mode="translate"` targets
  English only — useless for hi↔kn; we translate with Mayura.)
- **`config.update` re-arms auto-LID** (`language_code`) at the next utterance
  boundary — Phase 4 pair-lock no longer needs to tear down and restart the
  STT socket.
- No `confidence` on partials; `language_confidence` only on finals with `auto`.

---

## 1. Verified TTS streaming surface (bulbul:v3)

`client.text_to_speech_streaming.connect(model="bulbul:v3")`, then JSON
messages tagged by `type`:

| Direction | `type` tag | Fields | Use |
|---|---|---|---|
| → server | `config` | `target_language_code`, `speaker`, `pace` (0.5–2.0 on v3), `speech_sample_rate` (8000/16000/22050/24000), `output_audio_codec` ("mp3"), `min_buffer_size` (chars that trigger synthesis, default 50), `max_chunk_length` (default 150), `temperature`, `dict_id` | first message; **can be resent mid-stream** (auto-flushes buffer first) |
| → server | `text` | `data.text` | one committed translated segment |
| → server | `flush` | — | force synthesis of whatever is buffered |
| → server | `ping` | — | keepalive |
| ← server | `audio` | `data.audio` (b64), `data.content_type`, `data.request_id` | chunks to play out |
| ← server | `event` | `data.event_type: "final"`, `message`, `timestamp` | end-of-request marker |
| ← server | `error` | code/message | recovery |

Design-relevant knobs:

- **`min_buffer_size` low (~20–30)** so clause-sized segments synthesize
  immediately; call `flush` after each committed segment anyway.
- **`pace` is the catch-up lever**: resend `config` with `pace=1.15` when the
  playback queue backs up, back to 1.0 when drained (config resend is legal
  mid-stream and flushes first).
- Still **no server-side cancel** → barge-in = stop local playback, drop queue,
  rotate to spare socket (unchanged from HLD).
- Voices confirmed working on v3 (Phase 0): amit, priya, rohan, ritu.

Translation stays REST per segment: `client.text.translate(...)`, mayura:v1,
`mode=modern-colloquial`, `output_script=fully-native`. Segments are 5–10 words
→ 150–300 ms calls; the 1000-char splitter is never hit on the hot path.

---

## 2. Component architecture

Two always-on directional pipelines. No turn FSM in translation mode — the
half-duplex FSM survives only inside ACTIVE_MEDIATION.

```text
            LiveKit room (rider mic, driver mic — agent subscribes to mic tracks ONLY,
                          so translated audio can never feed back into STT)
                 │ 48 kHz frames
                 ▼
   ┌──────────────────────────────┐   one instance per participant (×2)
   │ AudioIngest                  │   resample → 16 kHz mono pcm_s16le,
   │                              │   100 ms frames → base64 audio_input
   └──────────────┬───────────────┘
                  ▼
   ┌──────────────────────────────┐   saaras:v3-realtime, stream_type=fast,
   │ SttSession                   │   endpointing=vad, silence_duration_ms≈350,
   │                              │   language auto → pinned via config.update
   └──────────────┬───────────────┘
                  │ transcript.partial / transcript.final / vad.*
                  ▼
   ┌──────────────────────────────┐   THE NEW MODULE (chunker.py) — pure logic,
   │ IncrementalChunker           │   offline-testable against recorded traces
   │  commit a segment when ANY:  │
   │  • stable prefix across 2    │   (local agreement k=2)
   │    consecutive partials      │
   │  • clause boundary token     │   hi: तो/फिर/लेकिन/और/क्योंकि/, ?/ ।
   │                              │   kn: ಮತ್ತು/ಆದರೆ/ಅಂದರೆ/ಆಮೇಲೆ/, ?
   │  • vad.speech_end OR         │
   │    transcript.final          │   (flush remainder, reconcile vs final)
   │  • max-lag timer ~1.2 s      │   (never hold longer)
   │  HOLD if inside digit run    │   (OTP straddling a boundary)
   └──────────────┬───────────────┘
                  │ CommittedSegment {seg_id, utt_idx, src_lang, text, t_committed}
                  ▼
   ┌──────────────────────────────┐   entity placeholder swap (OTP/gate/amount)
   │ SegmentTranslator            │   → mayura:v1 REST (modern-colloquial,
   │                              │     fully-native) → placeholder restore
   └──────────────┬───────────────┘
                  ▼
   ┌──────────────────────────────┐   bulbul:v3 WS per direction (+1 spare for
   │ TtsSession + PlaybackQueue   │   barge-in rotation); text→flush per segment;
   │                              │   backlog>2 segs → config pace=1.15;
   │                              │   mp3 decode → 48 kHz LiveKit AudioSource
   └──────────────┬───────────────┘
                  ▼
        agent-to-rider / agent-to-driver LiveKit tracks
        (client ducks the original remote voice to ~20% while agent track is live)
```

Both pipelines (hi→kn, kn→hi) run permanently once the language pair is locked;
distinct fixed voice per direction (e.g. amit for one, ritu for the other).

## 3. Chunker state machine (per speaker)

```text
state: {utt_idx, committed_prefix, last_partial, last_change_t, timer}

on transcript.partial(utt, text):
    if utt != utt_idx: reset(utt)                      # new utterance
    delta = text[len(committed_prefix):]
    if text == last_partial and stable_for >= 1 tick:  # local agreement k=2
        candidate = delta up to last clause boundary (or whole delta)
        if candidate and not ends_inside_digit_run(candidate):
            commit(candidate)
    else if clause_boundary in delta:
        commit(delta up to boundary)
    last_partial = text; restart max-lag timer

on max_lag_timeout (1.2 s since oldest uncommitted word):
    commit(whole uncommitted delta)                    # latency beats elegance

on vad.speech_end / transcript.final(utt, text):
    commit(text[len(committed_prefix):])               # remainder, from FINAL text
    # final may差 from partials → log divergence; audio already spoken is not retracted,
    # UI transcript row is replaced with the final text
```

Committed text is append-only (spoken audio can't be recalled). The on-screen
transcript is revisable: partials render grey/italic, finals replace them.

## 4. Cold start / pair lock (revised Phase 4)

1. Both STT sessions open with `language_code="auto"` — **chunking + translation
   are gated off** until lock; agent is silent.
2. `transcript.final` events carry `language` + `language_confidence`; 2
   consistent finals per side with confidence ≥ threshold → lock pair.
3. Send `config.update {language_code: "hi-IN"}` / `{"kn-IN"}` on the
   respective sockets (boundary-gated; no reconnect). Enable both pipelines.
4. Same language both sides → pipelines stay gated; drift engine keeps watching
   (unchanged product behavior: translation is one *capability*, activated
   passively when languages differ — full mediation still requires drift).

## 5. Internal event bus (LiveKit data channel → UI + drift engine)

```json
{"tag":"utterance.partial", "speaker":"driver", "utt":7, "text":"...", "lang":"kn-IN"}
{"tag":"utterance.final",   "speaker":"driver", "utt":7, "text":"...", "lang":"kn-IN", "conf":0.97, "start_s":41.2, "end_s":44.0}
{"tag":"segment.committed", "speaker":"driver", "seg":"7.2", "src":"...", "t_lag_ms":900}
{"tag":"segment.translated","speaker":"driver", "seg":"7.2", "tgt":"...", "t_translate_ms":240}
{"tag":"segment.spoken",    "speaker":"driver", "seg":"7.2", "t_ear_to_ear_ms":1650}
{"tag":"pair.locked",       "pair":["hi-IN","kn-IN"]}
{"tag":"tts.pace",          "direction":"kn→hi", "pace":1.15, "backlog":3}
{"tag":"agent.state",       "state":"PASSIVE_MONITOR|WATCH|OFFER_HELP|ACTIVE_MEDIATION"}
```

`t_ear_to_ear_ms` per segment drives the Phase 7 latency ticker. The drift
engine consumes `utterance.final` rows exactly as before — nothing about
drift/mediation changes; it just receives better-timed input.

## 6. Latency budget (targets to verify in Phase 1)

| Stage | Budget | Lever |
|---|---|---|
| LiveKit frame → STT ingest | 100–150 ms | 100 ms frames |
| audio → usable partial | 300–600 ms | `stream_type=fast` |
| chunker commit | 200–500 ms | k=2 agreement, 1.2 s max-lag |
| Mayura on clause | 150–300 ms | tiny payloads, keep-alive HTTP session |
| Bulbul first audio chunk | 300–500 ms | low `min_buffer_size` + explicit flush |
| decode + publish | ~100 ms | stream mp3 decode |
| **ear-to-ear while speaking** | **≈1.2–2.0 s** | |

Turn-end path stays fast too: `silence_duration_ms≈350` means the final lands
~350 ms after the speaker stops, and only the last remainder segment is pending.

## 7. Interaction with mediation (unchanged product, new plumbing)

- PASSIVE/translation mode: full-duplex, both pipelines live, humans manage
  turn-taking naturally because they hear each other (ducked) + translation.
- ACTIVE_MEDIATION: turn manager **pauses the two translation pipelines** and
  uses the same SttSession/TtsSession primitives half-duplex (agent question →
  targeted listener; barge-in via `vad.speech_start` from either mic).
- Barge-in on agent speech: `vad.speech_start` on a human mic while agent track
  is playing → stop playback, drop queue, rotate TTS socket (spare already
  configured), keep capturing the interrupter (their STT socket never closed).

## 8. Plan changes

- **Phase 1**: build `stt_realtime.py` on `speech_to_text_realtime_streaming`
  (not `speech_to_text_streaming`); log per-partial timestamps + prefix-revision
  rate against fixtures → sets agreement k and max-lag empirically. Add
  `chunker.py` + offline trace-replay test.
- **Phase 2**: wire partials→chunker→translate→TTS from day one (not
  sentence-after-END_SPEECH).
- **Phase 3**: half-duplex FSM demoted to mediation-only; add backlog/pace
  controller and ducking instead.
- **Phase 4**: pair lock via `config.update`, no socket restart.
- Fallback ladder unchanged; old `speech_to_text_streaming` (utterance-level)
  becomes Fallback 0 if the realtime endpoint proves unstable.

## 9. Open items to measure first (cheap, offline, Phase 1)

1. Real partial cadence and revision rate of `saaras:v3-realtime` `fast` vs
   `balanced` on hi/kn fixtures (drives chunker constants).
2. Whether `auto` sticky-language mislocks on code-mixed speech (hi speaker
   using Kannada place names) — if so, lock earlier from `/text-lid` on final.
3. Mayura quality on 5–10-word clauses vs full sentences (spot-check 20 chunks).
4. Bulbul v3 actual TTFB at `min_buffer_size=20` and behavior when `text` +
   `flush` arrive every ~1 s on a long-lived socket.
