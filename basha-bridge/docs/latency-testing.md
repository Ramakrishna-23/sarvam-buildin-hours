────────────────────────────────────────────────────────────────────────────────

Latency Testing — Basha Bridge

PM notes. Companion to [implementation-plan.md](./implementation-plan.md). Not pushed anywhere — local tracking only.

────────────────────────────────────────────────────────────────────────────────

## Why a single latency number is weak evidence

A single averaged "our latency is ~2s" claim is weak for the Technical Depth rubric criterion. The pipeline behaves differently depending on the scenario, and the current plan only tests one of these scenarios for a hard latency number — the others are tested for pass/fail intelligibility only. Rate latency per scenario bucket, with p50 *and* p90, not one average.

## The four scenario buckets

### 1. Clean turn-taking
One person talks, pause, other responds. What Phase 1's fixtures already test: `rolling lag < 2.5s per segment`. Baseline case, easiest.

### 2. Overlapping speech / cross-talk
Both rider and driver talking at once. Phase 3 currently only checks *intelligibility and no feedback loop* — no latency number attached. Gap: cross-talk stresses the self-echo guard (agent's own TTS must not leak into the other direction's STT) and segment-commit stability (harder to detect a clean `END_SPEECH` boundary with cross-talk on the mic). Needs its own measured rolling-lag number, not just pass/fail. See "Quantifying cross-talk" below for the test methodology.

### 3. Long uninterrupted utterance
Phase 2's test already checks lag *doesn't grow* with utterance length — this is really testing whether the stable-prefix segmenter commits clauses incrementally instead of silently falling back to re-transcribing the whole buffer. Worth its own row: a system fast on short utterances but degrading on long ones is a real risk (a rider giving a long rambling explanation is realistic, not an edge case).

### 4. Agent-prompt barge-in
Only applies if N1/N2 (mediation layer) ships. Different mechanism from #2: a human interrupting the *agent's own* spoken question, requiring local playback stop + fresh TTS stream (no server-side cancel available). Separate metric: time from interrupt-detected to agent-audio-actually-silent, not translation lag.

---

## Quantifying cross-talk (bucket 2) — test methodology

Live human testing isn't reproducible — two people talking over each other is different every take, so before/after comparisons after a fix are meaningless. Use synthetic, controlled overlap instead, built from fixtures already in the repo (`fixtures/*.wav`) since they're TTS-generated from known text — exact ground-truth transcript available for free.

### Construction

Feed two fixtures (e.g. `kn_pickup.wav` on the driver track, `hi_pickup.wav` on the rider track) into their respective pipelines with a controlled **start-offset** between them — not literally mixed into one file, since the architecture already keeps them as separate per-participant tracks. The offset is the independent variable:

- `offset = 0s` → fully simultaneous (worst case)
- `offset = full utterance length` → sequential (the existing clean-case test)
- intermediate points: 25%, 50%, 75% overlap

Also vary **which direction starts first** (kn-then-hi vs. hi-then-kn) to catch asymmetric bugs.

### What to measure — three separate things, don't conflate them

1. **Rolling lag** (timing) — source-audio-timestamp → translated-audio-emit-timestamp per segment, same metric as the clean case, bucketed by overlap offset.
2. **Transcript fidelity** (correctness) — diff the committed segment text against the known ground-truth text (from `scripts/make_fixtures.py`). Under cross-talk the failure mode might not be *slower*, it might be *wrong* (STT garbles words, or the self-echo guard leaks the other direction's TTS into the wrong transcript). A fast-but-wrong segment is worse than a slow-but-right one — one latency number hides this distinction entirely.
3. **Self-echo leak check** (specific to this architecture) — grep each direction's committed transcript for phrases that only exist in the *other* direction's translated TTS output. If driver-side STT ever picks up words that were actually the rider-side agent's own translated speech, that's the self-echo guard failing — a correctness bug that would otherwise just look like noisy latency data.

### Deliverable shape

Generalize `offline_pipeline.py` (Phase 1) into a small bench script: two fixtures + an offset parameter in, a row out:

```
{ offset, rolling_lag_p50, rolling_lag_p90, transcript_accuracy, echo_leak: bool }
```

Run across the offset matrix (0%, 25%, 50%, 75%, 100%) × both start-orders → the table becomes both the pitch evidence (Technical Depth) and a real regression test engineers can rerun after any pipeline change, not just a one-time demo number.

---

## Test cases per metric

All test cases use existing fixtures (`fixtures/hi_pickup.wav`, `hi_otp.wav`, `kn_pickup.wav`, `kn_eta.wav`) with known ground-truth text from `scripts/make_fixtures.py`. "Offset" = start-time gap between the two tracks as a % of the shorter fixture's length (0% = fully simultaneous, 100% = fully sequential).

### 1. Rolling lag

| ID | Setup | Pass criteria |
|---|---|---|
| TC-LAG-01 | Baseline: `kn_pickup.wav`, offset 100% (sequential) | p50 ≤ 2.5s, p90 ≤ 3.5s |
| TC-LAG-02 | `kn_pickup.wav` (driver) + `hi_pickup.wav` (rider), offset 50%, kn starts first | p50 ≤ 2.5s, p90 ≤ 4s |
| TC-LAG-03 | Same pair, offset 50%, **hi starts first** | Result within 15% of TC-LAG-02 (checks no direction-asymmetry bug) |
| TC-LAG-04 | Same pair, offset 0% (fully simultaneous) | Report p50/p90 — worst case, no hard ceiling yet, becomes the baseline for future regression comparison |
| TC-LAG-05 | Concatenate `hi_pickup.wav` + `hi_otp.wav` into one long utterance, offset 100% | lag of the *last* segment ≤ lag of the *first* segment + 0.3s (fails if lag grows with utterance length) |

### 2. Transcript fidelity

| ID | Setup | Pass criteria |
|---|---|---|
| TC-FID-01 | `kn_pickup.wav` alone, offset 100% | WER ≤ 10% vs. ground truth |
| TC-FID-02 | Same pair as TC-LAG-02, offset 50% | WER ≤ 25% (degraded but bounded) |
| TC-FID-03 | Same pair, offset 0% | Report WER — diagnostic only, no hard ceiling, tracked for regression |
| TC-FID-04 | `hi_otp.wav` ("मेरा ओटीपी चार सात दो नौ है"), any offset incl. 0% | OTP digits (4-7-2-9) appear correct in final TTS output — **0% tolerance, every offset** |
| TC-FID-05 | `hi_pickup.wav` ("गेट नंबर दो", "चाय की दुकान"), any offset | Landmark/gate reference transliterated, not translated or dropped, in every offset condition |

### 3. Self-echo leak check

| ID | Setup | Pass criteria |
|---|---|---|
| TC-ECHO-01 | `kn_pickup.wav` (driver) + `hi_pickup.wav` (rider), offset 50% | Driver-side transcript contains zero substrings from rider-side translated TTS output, and vice versa — **0% tolerance** |
| TC-ECHO-02 | Same pair, offset 0% (max stress) | Same zero-leak requirement — this is a structural guarantee, not expected to degrade gracefully under stress |
| TC-ECHO-03 | Rider track silent, driver speaks `kn_pickup.wav` | Rider-side STT produces no spurious transcript (catches raw audio bleed even without translated text to leak) |

### 4. Agent-prompt barge-in (interrupt-detected → audio-actually-silent)

Only applies once N1/N2 (mediation layer) ships — this is the agent's own spoken prompt being interrupted, not cross-talk between rider/driver. Metric definition: `t0` = VAD `START_SPEECH` fires on the human mic track *while* the agent's audio track is actively playing. `t1` = first moment the agent's output track amplitude drops below a silence threshold and stays there (measure at actual audio output via an amplitude/RMS check on the played track — not when the stop command was issued server-side, since already-buffered audio in the playback pipeline can continue briefly after `stop()` is called). Metric = `t1 − t0`.

Proposed target: **≤ 300ms**, since this is a local client-side stop (no network round trip required) and needs to feel instantaneous to a human — treat this threshold as a proposed UX bar to negotiate with the team, not a fixed spec.

| ID | Setup | Pass criteria |
|---|---|---|
| TC-BARGE-01 | Interrupt fired at ~20% into a synthesized agent prompt (e.g. "Please say the pickup gate...") | t1 − t0 ≤ 300ms |
| TC-BARGE-02 | Interrupt fired at ~80% into the same prompt (near end) | t1 − t0 ≤ 300ms, and within 20% of TC-BARGE-01's result (checks stop latency doesn't depend on how much audio was already buffered/in-flight) |
| TC-BARGE-03 | Same as TC-BARGE-01, under simulated network jitter/delay on the signaling path | t1 − t0 stays within 20% of TC-BARGE-01 — since the stop is local, it should **not** degrade with network conditions; a failure here means the "local stop" assumption is leaking a network dependency somewhere |
| TC-BARGE-04 | Rapid double-interrupt: human interrupts, agent reopens a fresh TTS stream almost immediately, human interrupts again within 1s | No audio from the *first* interrupted stream is audible after the *second* stop (checks for stale/ghost audio from an incompletely-torn-down stream, not just timing) |
| TC-BARGE-05 | Brief non-speech noise blip (e.g. 150ms) on the human mic while agent is speaking | Report whether it triggers a false-positive stop+reopen cycle, and the wasted latency cost if so — robustness check, not a hard pass/fail, but should be tracked since a jumpy VAD threshold makes the agent feel twitchy rather than responsive |

────────────────────────────────────────────────────────────────────────────────
