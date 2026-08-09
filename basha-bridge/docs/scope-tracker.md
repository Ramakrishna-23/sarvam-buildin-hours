────────────────────────────────────────────────────────────────────────────────

Scope & Cut-Line Tracker — Basha Bridge

PM-owned. Companion to [HLD.md](./HLD.md) and [initial-decisions.md](./initial-decisions.md). This is a living doc — update checkboxes and the decision log as the build progresses, don't let scope drift silently.

────────────────────────────────────────────────────────────────────────────────

## 1. Committed MVP scope (must work for the demo)

From HLD §8. If any of these slip, the demo script needs to change — flag immediately, don't quietly work around it.

- [ ] Two web clients (rider, driver) join a LiveKit room, human-to-human audio works.
- [ ] Backend agent worker subscribes to both audio streams (not as a LiveKit participant — local playback model per HLD §3/B).
- [ ] Streaming STT (Saaras v3) with `language_code=unknown` on both channels.
- [ ] Rule-based hard-trigger detection (explicit confusion, repeated question).
- [ ] Escalation ladder reaches at least `PASSIVE_MONITOR → OFFER_HELP → ACTIVE_MEDIATION`.
- [ ] Task mediator fills the demo-critical slots: `pickup_point`, `landmark`, `agreed_next_action`.
- [ ] Translation (Mayura v1) + TTS (Bulbul v3) round-trip, native script only (no romanized text to TTS).
- [ ] Agent audio played locally to the correct participant (targeted playback).
- [ ] `SUMMARIZE_AND_EXIT` fires and is audible to both sides.

## 2. Nice-to-have (cut first if behind schedule)

From HLD §8/§9. Ordered roughly by what to cut first.

- [ ] `SAFETY_ESCALATION` path (real handoff, not just the state existing).
- [ ] Full slot set (`driver_location`, `eta`, `otp`, `blocker`) beyond the 3 demo-critical slots above.
- [ ] `WATCH` as a distinct visible state (vs. collapsing straight to `OFFER_HELP`).
- [ ] Live transcript/status UI for judges to see agent reasoning.
- [ ] Structured LLM drift classifier (Sarvam-105B) — fallback is rules-only triggering.
- [ ] De-escalation back to `PASSIVE_MONITOR` mid-conversation (vs. one-shot mediation then stop).
- [ ] Any second language pair beyond the one locked demo pair.

## 3. Fallback triggers (decide the trigger time, not just the fallback)

From HLD §9. Each row needs a **decision deadline** — fill in real times once the hackathon schedule is known. The point is: the fallback decision is made *before* the deadline, not improvised at demo time.

| If... | ...by [TIME] | Then fallback to | Decided by |
|---|---|---|---|
| LiveKit streaming isn't stable | `[fill in]` | Chunked browser audio to backend (Fallback 1) | `[PM + eng]` |
| Chunked audio still unstable | `[fill in]` | Push-to-talk for agent mediation only (Fallback 2) | `[PM + eng]` |
| Full streaming pipeline not demo-ready | `[fill in]` | Text transcript + TTS playback only (Fallback 3) | `[PM + eng]` |
| Structured LLM classifier too slow/unreliable | `[fill in]` | Rules-only drift detection (no Stage B) | `[PM + eng]` |
| External driver-location API not integrated | `[fill in]` | Hardcoded/mocked slot values for demo | `[PM]` |

## 4. Component ownership (HLD §4, A–G)

Fill in as the team splits up work — keeps two builders from duplicating or dropping a component.

| Component | Description | Owner | Status |
|---|---|---|---|
| A. Rider/Driver Web Apps | LiveKit join, mic stream, playback, status UI | `[TBD]` | Not started |
| B. LiveKit WebRTC Room | Human-to-human audio transport | `[TBD]` | Not started |
| C. Voice Orchestrator / Agent Worker | Central backend: STT, drift, escalation, mediation, routing | `[TBD]` | Not started |
| D. Sarvam STT (Saaras v3) | Transcript + language detection | `[TBD]` | Not started |
| E. Drift Engine | Rules + signals → escalation output | `[TBD]` | Not started |
| F. Task Mediator | Slot tracking, allowed-action state machine | `[TBD]` | Not started |
| G. Translation + TTS (Mayura v1 / Bulbul v3) | Native-script translate + speech | `[TBD]` | Not started |

## 5. Decision log

Append-only. Every scope cut or fallback trigger pulled gets a line here — this is what you'll want when writing the pitch/retro afterward.

| Date/time | Decision | Reason | Decided by |
|---|---|---|---|
| `[fill in]` | e.g. "Cut OTP slot from demo scope" | e.g. "not needed for pickup-coordination narrative, saves a slot-filling turn" | `[name]` |

────────────────────────────────────────────────────────────────────────────────
