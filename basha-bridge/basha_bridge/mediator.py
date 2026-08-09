"""Task mediator: bounded LLM reasoning over sarvam-105b.

Two strict-JSON calls, both via forced tool-calling (the chat API has no
response_format, so a named tool_choice is how structure is enforced):

  assess()      — once per turn in monitor mode: frustration, contradiction,
                  safety, and task slot extraction
  next_action() — only in ACTIVE_MEDIATION: pick ONE action from the set the
                  app says is allowed, and supply the utterance to speak

The LLM never controls the conversation directly: the app computes the allowed
action set from slot state and derives the target listener from the action.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from sarvamai import AsyncSarvamAI

from .conversation import REQUIRED_SLOTS, SLOT_NAMES, ConversationMemory, Role
from .drift import Assessment

log = logging.getLogger(__name__)

MODEL = "sarvam-105b"
CALL_TIMEOUT_S = 8.0

# ── action set (Phase 6) ──────────────────────────────────────────────────

ACTIONS = (
    "ASK_RIDER_PICKUP_POINT",
    "ASK_RIDER_LANDMARK",
    "ASK_DRIVER_LOCATION",
    "ASK_DRIVER_FEASIBILITY",
    "ASK_DRIVER_ETA",
    "CONFIRM_WITH_RIDER",
    "CONFIRM_WITH_DRIVER",
    "SUMMARIZE_AND_EXIT",
    "SAFETY_ESCALATE",
)

ACTION_TARGET: dict[str, Role | str] = {
    "ASK_RIDER_PICKUP_POINT": "rider",
    "ASK_RIDER_LANDMARK": "rider",
    "ASK_DRIVER_LOCATION": "driver",
    "ASK_DRIVER_FEASIBILITY": "driver",
    "ASK_DRIVER_ETA": "driver",
    "CONFIRM_WITH_RIDER": "rider",
    "CONFIRM_WITH_DRIVER": "driver",
    "SUMMARIZE_AND_EXIT": "both",
    "SAFETY_ESCALATE": "both",
}

# Which slot each ASK action is trying to fill — used to compute the allowed set.
ACTION_FILLS = {
    "ASK_RIDER_PICKUP_POINT": "pickup_point",
    "ASK_RIDER_LANDMARK": "landmark",
    "ASK_DRIVER_LOCATION": "driver_location",
    "ASK_DRIVER_ETA": "eta",
    "ASK_DRIVER_FEASIBILITY": "agreed_next_action",
}


def allowed_actions(memory: ConversationMemory) -> list[str]:
    """App-enforced: only actions that move the task forward right now."""
    slots = memory.slots
    if slots.complete:
        return ["SUMMARIZE_AND_EXIT", "CONFIRM_WITH_RIDER", "CONFIRM_WITH_DRIVER"]
    out = [a for a, slot in ACTION_FILLS.items() if not slots.values.get(slot)]
    # confirmation is allowed once the two location slots exist but ETA doesn't
    if slots.values.get("pickup_point") and slots.values.get("driver_location"):
        out += ["CONFIRM_WITH_DRIVER", "CONFIRM_WITH_RIDER"]
    return out or ["SUMMARIZE_AND_EXIT"]


# ── tool schemas ──────────────────────────────────────────────────────────

ASSESS_TOOL = {
    "type": "function",
    "function": {
        "name": "report_assessment",
        "description": "Report whether the rider-driver conversation is failing, and extract task facts.",
        "parameters": {
            "type": "object",
            "properties": {
                "frustration": {
                    "type": "number",
                    "description": "Frustration level from 0.0 (calm) to 1.0 (very frustrated). Use the 0-1 scale only.",
                },
                "contradiction": {
                    "type": "boolean",
                    "description": "True if the two speakers state facts that conflict.",
                },
                "safety": {
                    "type": "boolean",
                    "description": "True only for a genuine safety emergency (accident, harassment, danger).",
                },
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Short quotes supporting the assessment.",
                },
                "slots": {
                    "type": "object",
                    "description": "Task facts stated so far. Omit or leave empty any that were not stated.",
                    "properties": {name: {"type": "string"} for name in SLOT_NAMES},
                },
            },
            "required": ["frustration", "contradiction", "safety", "evidence", "slots"],
        },
    },
}

ASSESS_SYSTEM = """You silently monitor a live rider-driver call about a ride pickup.
The two people may speak different Indian languages (Hindi/Kannada).

Report only what the transcript supports:
- frustration on a 0.0-1.0 scale
- contradiction: conflicting facts between the two speakers
- safety: ONLY a real emergency, never mere annoyance
- slots: facts actually stated (pickup_point, landmark, driver_location, eta,
  otp, blocker, agreed_next_action). Copy values verbatim; never invent one.

Different languages alone is NOT a problem. Always call report_assessment."""


def _mediate_tool(allowed: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": "choose_action",
            "description": "Choose the single next mediation action and the sentence to speak.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": allowed,
                        "description": "The one action to take next.",
                    },
                    "utterance": {
                        "type": "string",
                        "description": "One short sentence (max 20 words) to speak to the target, in simple English. It will be translated.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this action, in a few words.",
                    },
                },
                "required": ["action", "utterance", "reason"],
            },
        },
    }


MEDIATE_SYSTEM = """You are a voice mediator resolving a ride pickup between a rider and a driver
who do not share a language. You have joined the call.

Rules:
- Ask ONE short question at a time; never more than one sentence.
- Address only the person the action targets.
- Never invent facts. Use only what is in the transcript and known slots.
- Preserve numbers (OTP, gate numbers) exactly.
- When every required fact is known, SUMMARIZE_AND_EXIT with a one-sentence
  plan naming the pickup point and ETA.
Always call choose_action, and pick only from the allowed actions."""


@dataclass
class MediationAction:
    action: str
    target: str
    utterance: str
    reason: str


class TurnManager:
    """Enforces who speaks next: the agent asks one party a question and waits
    for that party before asking anything else."""

    def __init__(self, patience: int = 2) -> None:
        self.awaiting: str | None = None
        self.last_action: str | None = None
        self.patience = patience
        self._waited = 0

    def should_act(self, last_speaker: str) -> bool:
        if self.awaiting is None:
            return True
        if last_speaker == self.awaiting:
            self._waited = 0
            return True
        self._waited += 1
        if self._waited >= self.patience:  # addressed party went quiet; move on
            self._waited = 0
            return True
        return False

    def record(self, action: MediationAction) -> None:
        self.last_action = action.action
        self.awaiting = action.target if action.target in ("rider", "driver") else None


# ── calls ─────────────────────────────────────────────────────────────────


async def _call_tool(client: AsyncSarvamAI, system: str, user: str, tool: dict) -> dict:
    resp = await asyncio.wait_for(
        client.chat.completions(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": tool["function"]["name"]}},
            temperature=0.1,
            max_tokens=600,
        ),
        CALL_TIMEOUT_S,
    )
    calls = resp.choices[0].message.tool_calls
    if not calls:
        raise ValueError("model returned no tool call")
    return json.loads(calls[0].function.arguments)


async def assess(client: AsyncSarvamAI, memory: ConversationMemory) -> Assessment:
    """One classifier call per turn. Failures degrade to rules-only."""
    user = (
        f"Transcript so far:\n{memory.transcript(12)}\n\n"
        f"Known slots: {json.dumps(memory.slots.values, ensure_ascii=False)}\n"
        f"Still missing: {', '.join(memory.slots.missing_required) or 'none'}"
    )
    try:
        data = await _call_tool(client, ASSESS_SYSTEM, user, ASSESS_TOOL)
    except Exception as exc:
        log.warning("assess failed (%s); falling back to rules only", exc)
        return Assessment()

    frustration = float(data.get("frustration") or 0.0)
    if frustration > 1.0:  # models sometimes answer on a 0-10 scale
        frustration = min(frustration / 10.0, 1.0)
    slots = {k: v for k, v in (data.get("slots") or {}).items() if isinstance(v, str)}
    return Assessment(
        frustration=frustration,
        contradiction=bool(data.get("contradiction")),
        safety=bool(data.get("safety")),
        slots=slots,
    )


async def next_action(
    client: AsyncSarvamAI, memory: ConversationMemory
) -> MediationAction:
    """Pick the next mediation move, constrained to the app's allowed set."""
    allowed = allowed_actions(memory)
    user = (
        f"Transcript:\n{memory.transcript(12)}\n\n"
        f"Known slots: {json.dumps(memory.slots.values, ensure_ascii=False)}\n"
        f"Missing required: {', '.join(memory.slots.missing_required) or 'none'}\n"
        f"Allowed actions: {', '.join(allowed)}"
    )
    try:
        data = await _call_tool(client, MEDIATE_SYSTEM, user, _mediate_tool(allowed))
        action = data.get("action")
        utterance = (data.get("utterance") or "").strip()
        reason = (data.get("reason") or "").strip()
        if action not in allowed or not utterance:
            raise ValueError(f"action {action!r} not in allowed set")
    except Exception as exc:
        log.warning("next_action failed (%s); using deterministic fallback", exc)
        return _fallback_action(memory, allowed)

    if action == "SUMMARIZE_AND_EXIT":
        # the summary must name real slot values, not whatever the model wrote
        utterance = summary_text(memory)
    return MediationAction(action, ACTION_TARGET[action], utterance, reason)


FALLBACK_UTTERANCES = {
    "ASK_RIDER_PICKUP_POINT": "Where exactly should the driver pick you up?",
    "ASK_RIDER_LANDMARK": "What is one landmark next to you?",
    "ASK_DRIVER_LOCATION": "Where are you right now?",
    "ASK_DRIVER_ETA": "How many minutes until you reach the pickup point?",
    "ASK_DRIVER_FEASIBILITY": "Can you reach that pickup point?",
    "CONFIRM_WITH_RIDER": "Please confirm the pickup point.",
    "CONFIRM_WITH_DRIVER": "Please confirm you are heading there.",
    "SUMMARIZE_AND_EXIT": "Thank you, the pickup is arranged.",
}


def _fallback_action(memory: ConversationMemory, allowed: list[str]) -> MediationAction:
    """Deterministic path so a dead LLM never stalls the demo."""
    for slot in REQUIRED_SLOTS:
        if memory.slots.values.get(slot):
            continue
        for act, fills in ACTION_FILLS.items():
            if fills == slot and act in allowed:
                return MediationAction(
                    act, ACTION_TARGET[act], FALLBACK_UTTERANCES[act], "rule fallback"
                )
    act = allowed[0]
    text = summary_text(memory) if act == "SUMMARIZE_AND_EXIT" else FALLBACK_UTTERANCES.get(act, "Thank you.")
    return MediationAction(act, ACTION_TARGET[act], text, "rule fallback")


def summary_text(memory: ConversationMemory) -> str:
    v = memory.slots.values
    parts = []
    if v.get("pickup_point"):
        parts.append(f"pickup at {v['pickup_point']}")
    if v.get("landmark"):
        parts.append(f"near {v['landmark']}")
    if v.get("eta"):
        parts.append(f"driver arriving in {v['eta']}")
    return "Confirmed: " + ", ".join(parts) + "." if parts else "Pickup confirmed."
