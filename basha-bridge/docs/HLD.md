# HLD: Voice Intermediary Task Mediator

## 1. Objective

Build a live WebRTC-based AI mediator that silently monitors a two-person conversation, detects communication drift, and intervenes only when required.

Primary demo scenario:

> A rider and driver are trying to coordinate pickup. They may not share a language. The agent silently observes, detects that the task is breaking down, joins the conversation, and mediates until the pickup issue is resolved.

This is **not just a translator**. It is a **task mediator**.

---

## 2. Core decisions already taken

### Product behavior

- Do **not** pre-assume user languages.
- Agent starts in **silent monitor mode**.
- Language mismatch alone is **not drift**.
- Drift = language mismatch / confusion / contradiction / stalled task progress.
- Agent intervenes only after drift is detected.
- Once active, agent becomes a **task mediator**, not a relay translator.
- Demo should use **WebRTC**, not push-to-talk.
- Use Sarvam’s voice-agent stack where useful, but keep custom mediation logic.

---

## 3. High-level architecture

```text
┌──────────────────┐                  ┌──────────────────┐
│  Rider Web App   │◄── WebRTC Room ─►│ Driver Web App   │
│                  │     LiveKit      │                  │
└───────┬──────────┘                  └─────────┬────────┘
        │                                       │
        │ audio/control events                  │ audio/control events
        └───────────────┬───────────────────────┘
                        │
                        ▼
          ┌────────────────────────────┐
          │  Sarvam Voice Orchestrator │
          │  / Agent Worker            │
          └─────────────┬──────────────┘
                        │
       ┌────────────────┼────────────────┐
       ▼                ▼                ▼
┌────────────┐   ┌──────────────┐   ┌──────────────┐
│ Sarvam STT │   │ Drift Engine │   │ Task Mediator│
│ Saaras v3  │   │ + Rules      │   │ Sarvam-105B  │
└────────────┘   └──────────────┘   └──────┬───────┘
                                            │
                              ┌─────────────┴─────────────┐
                              ▼                           ▼
                       ┌──────────────┐            ┌────────────┐
                       │ Translation  │            │ Sarvam TTS │
                       │ Mayura v1    │            │ Bulbul v3  │
                       └──────────────┘            └────────────┘
```

---

## 4. Major components

### A. Rider / Driver Web Apps

Responsibilities:

- Join LiveKit WebRTC room.
- Stream user microphone.
- Receive other participant’s live audio.
- Send/allow audio monitoring by backend agent.
- Receive agent events:
  - `agent_joined`
  - `your_turn`
  - `wait`
  - `resolved`
- Play targeted agent TTS audio locally.
- Show live status/transcript/summary.

### B. LiveKit WebRTC Room

Responsibilities:

- Real-time human-to-human audio.
- Gives demo a live call feel.
- Provides separate participant identity.
- Enables future agent-as-room-participant model.

For MVP, LiveKit handles human audio. Agent audio can be sent separately to browsers and played locally.

### C. Sarvam Voice Orchestrator / Agent Worker

This is the central backend service.

Responsibilities:

- Subscribe to / receive user audio.
- Run STT per participant.
- Maintain transcript.
- Infer speaker language profile.
- Detect drift.
- Manage escalation state.
- Run task mediation state machine.
- Call Sarvam LLM with structured output.
- Call translation and TTS.
- Route agent messages/audio to the right user.

We should use Sarvam’s voice-agent setup where useful, especially for STT/TTS/LLM integrations, but the mediation logic remains custom.

### D. Sarvam STT

Use:

```text
Model: Saaras v3
Initial language: unknown
Mode: transcribe
```

Purpose:

- Generate transcript.
- Detect language.
- Support monitoring.
- Support VAD/turn detection where possible.

### E. Drift Engine

Inputs:

- Last N transcript turns.
- Speaker language profile.
- STT confidence/language probability.
- Confusion phrases.
- Repeated questions.
- Task slot progress.
- Contradictions.
- Silence / interruption signals.

Outputs:

```text
NO_OP
WATCH
OFFER_HELP
ACTIVE_MEDIATION
SAFETY_ESCALATION
```

### F. Task Mediator

Activated only after escalation.

Primary task for demo:

> Resolve ride pickup coordination.

Tracks slots:

```text
pickup_point
landmark
driver_location
rider_location
eta
otp
blocker
agreed_next_action
```

Allowed actions:

```text
ASK_RIDER_PICKUP_POINT
ASK_RIDER_LANDMARK
ASK_DRIVER_LOCATION
ASK_DRIVER_FEASIBILITY
ASK_DRIVER_ETA
CONFIRM_WITH_RIDER
CONFIRM_WITH_DRIVER
SUMMARIZE_AND_EXIT
SAFETY_ESCALATE
```

The LLM does not freely control the conversation. It returns structured JSON, and the app enforces the next action.

### G. Translation + TTS

Translation:

```text
Model: Mayura v1
Mode: modern-colloquial
Output script: fully-native
```

TTS:

```text
Model: Bulbul v3
Language: target participant’s inferred language
```

Important rule:

> Avoid romanized Indic text before TTS. Use native script for better speech quality.

---

## 5. Runtime flows

### Flow 1: Session start

```text
1. Rider and driver join LiveKit room.
2. Backend creates session state.
3. Agent starts in PASSIVE_MONITOR.
4. No agent audio is played.
```

### Flow 2: Passive monitoring

```text
1. Humans talk normally over WebRTC.
2. Agent silently transcribes both sides.
3. Language profiles are inferred.
4. Drift engine evaluates every turn.
5. Agent remains silent unless drift is detected.
```

### Flow 3: Drift detection

Example signals:

```text
Different languages detected
+ repeated confusion
+ pickup location unresolved
```

Decision:

```text
PASSIVE_MONITOR → ACTIVE_MEDIATION
```

Agent announces:

> “I’ll help resolve the pickup. Please speak one at a time.”

This is played in each participant’s language.

### Flow 4: Active task mediation

```text
1. Turn manager selects next speaker.
2. UI highlights that speaker.
3. Agent asks a targeted question.
4. Speaker responds.
5. STT transcribes response.
6. Task state updates.
7. Agent either asks next question, confirms, or summarizes.
```

Example:

```text
Agent to rider:
“Please say the pickup gate and one nearby landmark.”

Rider:
“Gate 2, near the tea shop.”

Agent to driver:
“The rider is at Gate 2 near the tea shop. Can you reach there?”

Driver:
“Yes, five minutes.”

Agent:
“Resolved. Driver will come to Gate 2 near the tea shop in five minutes.”
```

### Flow 5: Exit mediation

Exit when:

```text
required task slots are filled
+ both sides have a clear next action
```

Then:

```text
ACTIVE_MEDIATION → PASSIVE_MONITOR
```

Agent says:

> “Okay, I’ll step back now.”

---

## 6. State machine

```text
PASSIVE_MONITOR
    ↓
WATCH
    ↓
OFFER_HELP
    ↓
ACTIVE_MEDIATION
    ↓
RESOLVED
    ↓
PASSIVE_MONITOR
```

Emergency path:

```text
ANY_STATE → SAFETY_ESCALATION
```

---

## 7. Sarvam usage

We should explicitly position the stack as Sarvam-first:

```text
Saaras v3      → speech-to-text / language detection
Sarvam-105B    → structured drift + mediation reasoning
Mayura v1      → Hindi/Kannada translation
Bulbul v3      → text-to-speech
Sarvam voice-agent setup → LiveKit/STT/TTS integration patterns
```

Use the Sarvam voice-agent setup where it reduces implementation effort, but keep our own orchestrator for:

- silent monitoring
- drift detection
- escalation policy
- task mediation
- targeted playback
- turn management

---

## 8. MVP architecture decision

For hackathon MVP:

```text
LiveKit for human-to-human WebRTC.
Custom Sarvam Voice Orchestrator for agent intelligence.
Targeted agent audio played locally in each browser.
```

This avoids the complexity of making the agent a full LiveKit participant immediately.

Future/north-star:

```text
Agent joins LiveKit room as a real participant,
subscribes to both tracks,
publishes targeted audio tracks,
and supports production telephony integrations.
```

---

## 9. Fallback strategy

If live streaming becomes unstable:

```text
Fallback 1: keep LiveKit call, use chunked browser audio to backend.
Fallback 2: fallback to push-to-talk for agent mediation only.
Fallback 3: use text transcript + TTS playback instead of full streaming.
```

Push-to-talk should be fallback, not primary demo.

---

## 10. Key HLD risks

| Risk | Mitigation |
|---|---|
| Voice-agent SDK assumes one user ↔ one agent | Use SDK primitives, custom orchestrator |
| Audio routing complexity | Play agent audio locally per browser |
| False-positive intervention | Use WATCH/OFFER_HELP before active mediation |
| Latency | Keep agent utterances short; avoid big LLM on every turn |
| TTS barge-in | Stop playback locally; close/reopen TTS stream |
| Entity corruption | Preserve OTP/gate/vehicle/amount in task state |

---

## 11. Next step: LLD

LLD should define:

- exact session schema
- WebSocket event schema
- transcript format
- drift scoring logic
- task slot schema
- mediator JSON schema
- turn manager state machine
- Sarvam API call sequence
- fallback paths
- demo script flow
