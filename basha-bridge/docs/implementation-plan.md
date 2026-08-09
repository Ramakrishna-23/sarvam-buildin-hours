# Basha Bridge — Phased Implementation Plan

Source: `docs/architecture.html` (HLD) · `docs/initial-decisions.md` · mentor feedback (2026-08-09)

**Product shape after mentor review:** always-on **simultaneous relay** (live dubbing, ~2 s rolling lag) is the baseline. Drift-gated activation and task mediation are layered on top after the baseline ships.

**Baseline rule:** language mismatch detected → relay starts. Same language → agent stays silent.

Rule: **every phase ends with a runnable, testable artifact.** If time runs out mid-phase, the previous phase is still demoable.

---

# BASELINE — hosted live interpreter

## Phase 0 — Scaffold & connectivity ✅ done

`uv` project, `--check` CLI (Sarvam + LiveKit green), 4 fixture WAVs (hi/kn, OTP + place name, 16 kHz mono PCM), STT round-trip validated with `language_code="unknown"`.

---

## Phase 1 — Sarvam client layer + partials spike (~2–3 h)

**Goal:** every Sarvam API wrapped and proven against fixtures; the simultaneous-relay question answered with data.

Use the official `sarvamai` SDK (`AsyncSarvamAI`); raw `websockets` only where the SDK falls short.

Tasks:
- **Partials spike (do first — informs everything):** feed a fixture in real-time-sized chunks to `speech_to_text_realtime_streaming` and `speech_to_text_streaming`; measure partial-transcript cadence and stability. Decide: true partials vs rolling 1.5–2 s re-transcribed windows (cookbook Live_Video_Transcription pattern) as fallback
- `stt_stream.py` — chosen streaming client, saaras:v3, `mode=transcribe`, `vad_signals=true`, 16 kHz pcm_s16le
- `segmenter.py` — stable-prefix / clause-boundary commit policy (commit when text unchanged across K partials or at `।` `,` `.` `?` `!`); target commit horizon 0.6–1 s
- `stt_rest.py` — batch STT `language_code=unknown` → language + confidence (proven in Phase 0); `/text-lid` re-check
- `translate.py` — mayura:v1, `modern-colloquial`, `fully-native`; OTP regex lock (digits never translated); glossary placeholder swap + restore
- `transliterate.py` — names/place names cross scripts without translation
- `tts.py` — bulbul:v3 per-segment synthesis into a playback queue; SDK `text_to_speech_streaming`/`convert_stream` confirmed to exist — verify cancel behavior; v3 speaker set only (confirmed working: amit, priya, rohan, ritu)

**Artifact:** `uv run python -m basha_bridge.offline_pipeline fixtures/kn_pickup.wav`
→ streams the WAV as if live → prints committed segments as they emerge → translated segments → writes `out.wav` → per-stage latency table incl. **rolling lag** (audio-time → translated-audio-time per segment).
**Test:**
- committed segments emerge *while* audio is still streaming (not after)
- OTP digits survive verbatim; place name transliterated not translated
- measured rolling lag < 2.5 s per segment on fixtures

---

## Phase 2 — One-direction live relay over LiveKit (~2–3 h)

**Goal:** speak Kannada on one device, hear Hindi on the other *while still speaking*.

Tasks:
- Agent process (LiveKit Agents, Python): join room, subscribe to one participant, publish one agent audio track
- Wire Phase 1 loop live: track PCM → resample 48→16 kHz → streaming STT → segmenter → translate → TTS → playback queue → AudioSource
- Hardcode kn→hi; original audio ducked to ~20% on the listener side (client-side gain)
- Minimal frontend: join page with `?role=customer|driver` links (Vite + LiveKit JS SDK)

**Artifact:** two devices in one room; continuous Kannada in → rolling Hindi out.
**Test:** first translated audio starts before the speaker finishes a long sentence; rolling lag ≤ 2.5 s; lag does not grow with utterance length.

---

## Phase 3 — Full-duplex: both directions + collision handling (~2 h)

**Goal:** natural two-way conversation, both relays live simultaneously.

Tasks:
- Mirror pipeline hi→kn; fixed distinct voice per direction
- Two independent playback queues (agent-to-rider, agent-to-driver tracks; selective subscription by role)
- Collision policy: both humans talking at once → both relays keep running (full-duplex, no turn gating); listener-side ducking keeps it intelligible
- Self-echo guard: agent's own TTS output must not feed back into the other direction's STT (subscribe only to human tracks — structural, verify)

**Artifact:** live two-way translated conversation between two devices.
**Test:** back-and-forth with overlaps stays intelligible; no feedback loops; per-direction lag ≤ 2.5 s.

---

## Phase 4 — Language detect, pair lock, relay gate (~1 h)

**Goal:** cold start with no assumed languages; relay only when needed.

Tasks:
- Buffer first utterance per side → REST STT `language_code=unknown` → `/text-lid` confirm; lock pair after 2 consistent utterances per side
- **Mismatch → relay starts** (baseline rule). Same language → agent stays silent for the whole call
- Dominant-language lock: code-mixed borrowings don't flip the lock

**Artifact:** cold-start session; agent infers languages itself.
**Test:**
- hi + kn speakers → pair locks, relay begins within ~2 exchanges
- hi + hi speakers → agent never emits audio

---

## Phase 5 — Hosted deployment (~2 h)

**Goal:** rubric criterion 3 — hosted, stable, anyone can use it without us present.

Tasks:
- Agent worker on Fly.io / Railway / Render (Docker, secrets, auto-restart; one agent per room); **deploy in India region** (agent↔Sarvam↔LiveKit hops all short)
- Frontend on Vercel / CF Pages: landing page → "Create session" → room + two role links
- Session TTL / room cleanup, rate limit on session creation, health endpoint
- README with public URL + 3-step try-it instructions

**Artifact:** public URL a judge can open cold — create session, share second link, talk.
**Test:** two phones, fresh browsers, no local setup → working relayed call; worker survives session churn; health green after 1 h idle.

---

## Phase 6 — Demo polish (~1–2 h)

**Goal:** judge-ready baseline.

Tasks:
- Live bilingual transcript UI (segments appear as committed), rolling-lag ticker, language-pair badge
- Dry runs ×2 + fallback video

**Artifact:** rehearsed demo + recording. **Baseline ships here.**

---

# NICE-TO-HAVE LAYER — in priority order, each independently demoable

## N1 — Drift-gated activation (~2–3 h)

Restores the "silent unless needed" intelligence: mismatch alone no longer auto-starts relay.

- Ladder: `PASSIVE_MONITOR → WATCH (mismatch, silent) → OFFER_HELP (one-shot bilingual offer) → RELAY`
- Hard triggers (code): confusion lexicon (hi/kn), wake phrase, repeated question, >6 s post-question silence
- Soft signals + strict-JSON drift classifier (sarvam-105b-conversations, async, may lag one turn)
- Handles the partial-comprehension case: mixed languages but progressing → stays silent

**Artifact:** `uv run python -m basha_bridge.replay scenarios/*.json` — scripted transcripts assert ladder transitions (progressing mixed-language convo stays in WATCH; confusion ×2 → OFFER_HELP).

## N2 — Task mediation layer (~3 h)

When relay alone isn't resolving the task:

- Slot store: `pickup_point · landmark · driver_location · eta · otp_exchanged · blocker · agreed_next_action` (filled async by the drift classifier call)
- Allowed-action set: `ASK_RIDER_* · ASK_DRIVER_* · CONFIRM_* · SUMMARIZE_AND_EXIT · SAFETY_ESCALATE`; turn manager enforces who hears what; sarvam-105b slow path for disputes / "agent, tell him…"
- Exit: slots filled → bilingual summary → back to relay

**Artifact:** scripted stalling conversation → mediation Q&A → slots filled → bilingual summary.

## N3 — Robustness extras

Safety-keyword escalation from any state · mid-call language re-lock on sustained switch · slot panel UI via data channel · barge-in refinement for mediation prompts.

---

## Pitch checklist (rubric criteria 1 & 4)

Deliverable: README section + 2-min spoken narrative. Must cover:

- [ ] **Problem in one line:** rider and driver don't share a language; pickup coordination fails → cancellations, lost revenue, safety risk
- [ ] **Simultaneous, not consecutive:** translation flows while you speak (~2 s behind) — not a walkie-talkie translator; demo a long sentence to prove lag doesn't grow
- [ ] **Knows when it's not needed:** same-language call → agent detects it and stays completely silent (demo this contrast)
- [ ] **Why Sarvam specifically:** colloquial code-mixed Indic STT (Saaras), native-script TTS (Bulbul), Indic-pair colloquial translation (Mayura), strict-JSON reasoning (105b); hi↔kn are both SOV → clause-level simultaneous translation stays coherent
- [ ] **Sarvam drives the product:** every pipeline stage is a Sarvam model
- [ ] **Business path:** cancellation cost numbers; expansion to delivery/home services/telehealth; prod path = Exotel/Twilio masked calls (8 kHz parameterized)
- [ ] **Technical depth:** stable-prefix segment commit, full-duplex dual pipelines, entity locks (OTP never translated), rolling-lag telemetry — and (if N1/N2 land) drift-gated escalation + constrained mediation

---

## Fallbacks

| Breaks | Fall back to |
|---|---|
| True STT partials weak/unstable | rolling 1.5–2 s re-transcribed windows (+~1 s lag, proven cookbook pattern) |
| Streaming TTS issues | per-segment REST TTS into playback queue (+~0.3 s) |
| Full-duplex audio chaos | half-duplex turn gating (walkie-talkie feel, still live) |
| LiveKit agent audio issues | push-to-talk relay only |
