# Basha Bridge — Phased Implementation Plan

Source: `docs/architecture.html` (HLD) · `docs/initial-decisions.md`

Rule: **every phase ends with a runnable, testable artifact.** If time runs out mid-phase, the previous phase is still demoable.

---

## Phase 0 — Scaffold & connectivity (~1 h)

**Goal:** environment proven before writing pipeline code.

Tasks:
- `uv` project scaffold: `basha_bridge/` package, `.env` (`SARVAM_API_KEY`, `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`)
- Record/collect fixture WAVs: 2× Hindi, 2× Kannada — at least one containing an OTP and a place name
- `--check` command: hits Sarvam REST STT with a fixture, mints a LiveKit token and joins/leaves a room

**Artifact:** `uv run basha-bridge --check`
**Test:** both checks print green; fixture WAVs committed under `fixtures/`.

---

## Phase 1 — Sarvam client layer, offline (~2–3 h)

**Goal:** every Sarvam API wrapped and proven against fixtures, no WebRTC involved.

Use the official `sarvamai` Python SDK (`AsyncSarvamAI`) as the default client; drop to raw `websockets` only where the SDK falls short.

Tasks:
- `stt_stream.py` — SDK `speech_to_text_streaming.connect()`, saaras:v3, `mode=transcribe`, `vad_signals=true`, 16 kHz pcm_s16le; check if `language_code="unknown"` works well enough on streaming to simplify Phase 4
- `stt_rest.py` — SDK batch STT with `language_code=unknown` → detected language + confidence; `/text-lid` re-check
- `translate.py` — SDK translate, mayura:v1, `modern-colloquial`, `output_script=fully-native`; Indic-aware sentence splitter (1000-char limit); glossary/OTP placeholder swap + restore
- `transliterate.py` — SDK transliterate for names/place names
- `tts_stream.py` — bulbul:v3 streaming; SDK has `text_to_speech_streaming` + `convert_stream` (confirmed in Phase 0) — verify client-side cancel for barge-in; raw WS fallback only if socket control is insufficient. Note: bulbul:v3 has its own speaker set (v2 voices rejected); confirmed working: amit, priya, rohan, ritu
- `chat.py` — sarvam-105b-conversations with strict JSON schema (SDK chat, or OpenAI SDK against `api.sarvam.ai/v1` if JSON-schema mode is easier there)

**Artifact:** `uv run python -m basha_bridge.offline_pipeline fixtures/kn_otp.wav`
→ prints transcript → native-script Hindi translation → writes `out.wav` → prints per-stage latency.
**Test:**
- kn fixture → intelligible Hindi audio
- OTP digits survive translation verbatim
- place name transliterated, not translated
- latency table gives baseline for the <2 s budget

---

## Phase 2 — One half-pipeline live over LiveKit (~2–3 h)

**Goal:** first live audio: speak Kannada on one device, hear Hindi on another.

Tasks:
- Agent process using LiveKit Agents (Python): join room, subscribe to "driver" participant, publish agent audio track
- Wire Phase 1 clients into a live pipeline: STT WS → translate (sentence-streamed) → TTS WS → publish
- Hardcode kn→hi; no turn manager yet
- Minimal frontend: join page with `?role=customer|driver` links (Vite + LiveKit JS SDK)

**Artifact:** two browsers/devices in one room; Kannada in → Hindi out.
**Test:** first translated audio < 2.5 s after end of speech; long utterance streams sentence-by-sentence.

---

## Phase 3 — Mirror pipeline, turn manager, barge-in (~2 h)

**Goal:** natural back-and-forth in both directions.

Tasks:
- Mirror pipeline B (hi→kn), distinct fixed voice per direction
- Half-duplex FSM: `IDLE → CAPTURING(party) → PROCESSING → SPEAKING → IDLE`; ~0.5 s END_SPEECH debounce
- Barge-in: `START_SPEECH` from the listening party → stop publishing, drop queued TTS chunks, rotate to spare TTS socket, capture interrupter

**Artifact:** live two-way translated conversation.
**Test:**
- alternating turns work without pipelines talking over each other
- interrupting the agent mid-playback cuts audio and captures the interrupter's speech losslessly

---

## Phase 4 — Language detection & pair lock (~1 h)

**Goal:** cold start with no assumed languages.

Tasks:
- Buffer first utterance per side (between VAD START/END) → REST STT `language_code=unknown` → `/text-lid` confirm
- Lock pair after 2 consistent utterances per side; restart streaming STT with fixed codes
- Same language both sides → agent stays passive permanently

**Artifact:** cold-start session; agent infers hi/kn itself.
**Test:**
- hi + kn speakers → correct pair lock, translation begins
- hi + hi speakers → agent never emits audio

---

## Phase 5 — Drift engine & escalation ladder (~2 h)

**Goal:** the agent knows *when* to intervene. Testable **offline** via a replay harness — no audio needed.

Tasks:
- Rolling bilingual memory (last N turns) + static context (trip ID, OTP, glossary)
- Task slot store: `pickup_point · landmark · driver_location · eta · otp_exchanged · blocker · agreed_next_action`
- Hard triggers in code: confusion lexicon (hi/kn), wake phrase, repeated question (fuzzy vs last 3 turns), >6 s post-question silence, safety keywords
- Soft signals: differing stable languages, low STT confidence, empty key slots after K turns, contradiction, frustration
- One strict-JSON classifier call per turn (async, may lag one turn): `{drift, evidence[], task_progress, frustration, safety}`
- Ladder FSM: `PASSIVE_MONITOR → WATCH → OFFER_HELP → ACTIVE_MEDIATION → SAFETY_ESCALATION`

**Artifact:** `uv run python -m basha_bridge.replay scenarios/pickup_fail.json`
— feeds a scripted transcript turn-by-turn, prints state transitions + filled slots.
**Test:** scenario files assert expected transitions:
- confusion ×2 + empty pickup slot → `OFFER_HELP`
- smooth same-language chat → stays `PASSIVE_MONITOR`
- safety keyword → `SAFETY_ESCALATION` from any state

---

## Phase 6 — Active mediation (~2 h)

**Goal:** the agent resolves the pickup task end-to-end.

Tasks:
- `OFFER_HELP`: one-shot bilingual TTS offer; affirmative reply → `ACTIVE_MEDIATION`
- Allowed-action mediation: LLM returns one of `ASK_RIDER_PICKUP_POINT · ASK_RIDER_LANDMARK · ASK_DRIVER_LOCATION · ASK_DRIVER_ETA · CONFIRM_WITH_RIDER · CONFIRM_WITH_DRIVER · SUMMARIZE_AND_EXIT · SAFETY_ESCALATE`; turn manager enforces who hears what
- Slow path: low confidence / dispute / "agent, tell him…" → one sarvam-105b call, constrained to the same action set
- Exit: required slots filled + agreed next action → bilingual summary → back to `PASSIVE_MONITOR`

**Artifact:** live scripted demo run: failing hi/kn conversation → offer → mediation Q&A → slots filled → bilingual `SUMMARIZE_AND_EXIT`.
**Test:** full happy-path script completes; final summary contains pickup point, landmark, ETA in both languages.

---

## Phase 7 — Demo polish (~2 h)

**Goal:** judge-ready.

Tasks:
- Live bilingual transcript UI, escalation badge, slot panel (LiveKit data channel)
- Low-volume (~20%) original audio under the translation
- Latency ticker on screen
- 2× full dry runs; record a fallback video

**Artifact:** rehearsed demo + fallback recording.
**Test:** dry run completes twice without manual intervention.

---

## Phase 8 — Hosted deployment (~2 h)

**Goal:** rubric criterion 3 — hosted, stable, anyone can use it without us present.

Tasks:
- Deploy agent worker (Fly.io / Railway / Render): Dockerfile, env secrets, auto-restart; one agent process spawned per room
- Deploy frontend (Vercel / CF Pages) with a landing page: "Create session" → generates room + two role links (`?role=customer|driver`)
- LiveKit Cloud already hosts the RTC plane — verify token minting works from the deployed backend
- Basic hardening: session TTL / room cleanup, rate limit on session creation, health endpoint
- README with public URL + 3-step "try it yourself" instructions

**Artifact:** public URL a judge can open cold — create a session, share the second link to another device, talk.
**Test:**
- two phones, fresh browsers, no local setup → working mediated call
- agent worker survives a session ending and serves the next one
- health endpoint green after 1 h idle

---

## Pitch checklist (rubric criteria 1 & 4)

Deliverable: README section + 2-min spoken narrative. Must cover:

- [ ] **Problem in one line:** rider and driver don't share a language; pickup coordination fails → cancellations, lost revenue, safety risk
- [ ] **Not a translator app:** the agent is *silent by default* and intervenes only on detected drift — demo this contrast explicitly (same-language call → agent never speaks)
- [ ] **Why Sarvam specifically:** colloquial code-mixed Indic STT (Saaras), native-script TTS quality (Bulbul), Indic pair translation with colloquial register (Mayura), strict-JSON reasoning (105b) — a generic LLM stack degrades on every one of these
- [ ] **Sarvam drives the product:** every stage of the pipeline is a Sarvam model; nothing works without them
- [ ] **Business path:** ride-hailing pickup failure → cancellation cost numbers; expansion to delivery, home services, telehealth; prod path = Exotel/Twilio masked-number calls (architecture already parameterized for 8 kHz telephony)
- [ ] **Technical depth talking points:** hybrid drift engine (rules + slots + bounded LLM), half-duplex turn manager, barge-in with spare-socket rotation, entity locks (OTP never translated)

---

## Fallbacks (from HLD §risks)

| Breaks | Fall back to |
|---|---|
| Streaming STT/TTS unstable | chunked browser audio → REST STT / non-streaming TTS |
| LiveKit agent audio issues | push-to-talk for agent mediation only |
| Everything live | text transcript + TTS playback demo |
