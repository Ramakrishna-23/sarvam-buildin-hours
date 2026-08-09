 ────────────────────────────────────────────────────────────────────────────────

 Voice Intermediary Agent — Decisions So Far

 Core product framing

 We are not building a simple translator. We are building a silent AI mediator for live conversations that only intervenes when the conversation starts
 failing.

 Initial example: rider/driver interaction where people may not share a language or may misunderstand each other during pickup coordination.

 ────────────────────────────────────────────────────────────────────────────────

 Questions considered and decisions taken

 ### 1. Should we assume users already don’t know each other’s language?

 Decision: No.

 We should not pre-assume “Hindi customer vs Kannada driver.” The agent should begin as a silent monitor and infer language/comprehension issues from
 the live conversation.

 Sarvam grounding: STT supports language_code="unknown" and can return detected language + confidence.

 ────────────────────────────────────────────────────────────────────────────────

 ### 2. Does language mismatch automatically mean drift?

 Decision: No.

 Different detected languages are only a signal. Drift requires evidence that the conversation is not progressing.

 Example drift evidence:
 - “I don’t understand”
 - repeated question
 - irrelevant response
 - pickup point still unresolved
 - contradiction
 - frustration/safety concern

 So:

 ```text
   language mismatch alone ≠ intervention
   language mismatch + failed comprehension/task progress = drift
 ```

 ────────────────────────────────────────────────────────────────────────────────

 ### 3. How should drift be detected?

 Decision: Use a hybrid approach:

 ```text
   rules + task state + structured LLM classifier
 ```

 Not prompt-only.

 Hard triggers:
 - explicit confusion
 - manual help
 - safety issue
 - repeated failed exchange

 Soft triggers:
 - different stable languages
 - low STT confidence
 - missing pickup/landmark/ETA
 - contradiction
 - long silence after question
 - frustration

 Sarvam grounding: Chat Completions support strict JSON schema, so the LLM can return bounded classifications.

 ────────────────────────────────────────────────────────────────────────────────

 ### 4. How should escalation work?

 Decision: Use escalation levels, not binary intervention.

 ```text
   PASSIVE_MONITOR
   → WATCH
   → OFFER_HELP
   → ACTIVE_MEDIATION
   → SAFETY_ESCALATION
 ```

 This prevents the agent from jumping in too early.

 ────────────────────────────────────────────────────────────────────────────────

 ### 5. What happens after intervention?

 Decision: The agent should be a task mediator, not a relay translator.

 The task mediator’s job is to resolve the service task, e.g.:

 │ “Get rider and driver to agree on pickup location/action.”

 For ride pickup, the agent should track slots like:

 ```text
   pickup point
   landmark
   driver location
   ETA
   OTP
   blocker
   agreed next action
 ```

 ────────────────────────────────────────────────────────────────────────────────

 ### 6. How do we keep mediation deterministic?

 Decision: Use a turn manager/state machine.

 The LLM should not freely control the conversation. It should only choose from allowed actions like:

 ```text
   ASK_RIDER_LANDMARK
   ASK_DRIVER_LOCATION
   CONFIRM_WITH_DRIVER
   CONFIRM_WITH_RIDER
   SUMMARIZE_AND_EXIT
   SAFETY_ESCALATE
 ```

 The app enforces who speaks next.

 ────────────────────────────────────────────────────────────────────────────────

 ### 7. Push-to-talk, telephony, or WebRTC?

 Decision: WebRTC for the strong demo.

 Push-to-talk feels too much like a translation app. Actual telephony is powerful but risky for hackathon timing.

 WebRTC gives:
 - live conversation feel
 - separate participant streams
 - easier speaker attribution
 - easier targeted playback
 - easier turn control

 Likely direction: LiveKit-style WebRTC room, with an agent/orchestrator silently monitoring and intervening when needed.

 ────────────────────────────────────────────────────────────────────────────────

 Sarvam-specific grounding from docs

 Confirmed via ctx7/Sarvam docs:

 - STT supports auto language detection using language_code="unknown".
 - Streaming STT supports VAD events like START_SPEECH / END_SPEECH.
 - Chat Completions support strict structured JSON outputs.
 - Translate supports Hindi/Kannada and style/script control.
 - TTS quality is better with native Indic script, not romanized text.
 - TTS streaming does not support true server-side cancel; barge-in must stop playback locally and reopen stream.

 ────────────────────────────────────────────────────────────────────────────────

 Remaining items for HLD/LLD

 These can be handled during architecture planning:

 - WebRTC topology
 - LiveKit vs custom WebRTC
 - audio routing
 - targeted playback
 - task state schema
 - entity preservation
 - latency budget
 - fallback path
 - MVP vs nice-to-have scope
