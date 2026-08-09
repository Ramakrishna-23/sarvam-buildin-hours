"""Drift engine: rule signals + escalation ladder.

Pure logic — no network, no audio. The LLM assessment (frustration,
contradiction, slot extraction) is injected as an optional input so the whole
ladder is replayable offline against scripted transcripts.

Product rule from docs/initial-decisions.md:
    language mismatch alone  != drift
    language mismatch + failed comprehension/task progress = drift
so LANG_MISMATCH is weighted below the WATCH threshold and can never escalate
on its own.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum

from .conversation import ConversationMemory, Turn

WINDOW = 6  # turns considered "recent"


class State(str, Enum):
    PASSIVE_MONITOR = "PASSIVE_MONITOR"
    WATCH = "WATCH"
    OFFER_HELP = "OFFER_HELP"
    ACTIVE_MEDIATION = "ACTIVE_MEDIATION"
    SAFETY_ESCALATION = "SAFETY_ESCALATION"
    RESOLVED = "RESOLVED"


# ── lexicons ──────────────────────────────────────────────────────────────

CONFUSION = (
    # hindi
    "समझ नहीं", "समझा नहीं", "क्या बोल", "क्या कह", "फिर से बोल", "दोबारा बोल",
    "मुझे नहीं पता", "कुछ समझ",
    # kannada
    "ಅರ್ಥ ಆಗಲಿಲ್ಲ", "ಅರ್ಥವಾಗಲಿಲ್ಲ", "ಏನು ಹೇಳ್ತಿದೀರಾ", "ಏನಂತ", "ಗೊತ್ತಾಗಲಿಲ್ಲ",
    "ಮತ್ತೆ ಹೇಳಿ",
    # english fallbacks common in code-mixed speech
    "don't understand", "not understand", "what are you saying", "come again",
)

WAKE = (
    "मदद कर", "हेल्प", "एजेंट", "ಸಹಾಯ ಮಾಡಿ", "ಏಜೆಂಟ್", "help me", "agent",
)

SAFETY = (
    "बचाओ", "पुलिस", "एक्सीडेंट", "दुर्घटना", "खतरा", "छेड़",
    "ಪೊಲೀಸ್", "ಅಪಘಾತ", "ಅಪಾಯ", "ಕಾಪಾಡಿ",
    "police", "accident", "emergency", "unsafe", "harass",
)

QUESTION_MARKS = ("?", "क्या", "कहाँ", "कहां", "कब", "कौन", "ಎಲ್ಲಿ", "ಯಾವಾಗ", "ಏನು")

# ── signal weights ────────────────────────────────────────────────────────

WEIGHTS = {
    "CONFUSION": 2.0,
    "REPEATED_QUESTION": 1.5,
    "CONTRADICTION": 1.5,
    "FRUSTRATION": 2.0,  # scaled by the LLM's 0-1 score
    "SILENCE_AFTER_QUESTION": 1.0,
    "SLOTS_STALLED": 1.0,
    "LANG_MISMATCH": 0.5,  # context only — below WATCH on its own
}
CAPS = {"CONFUSION": 2, "REPEATED_QUESTION": 2, "CONTRADICTION": 1}

WATCH_AT = 2.0
OFFER_AT = 4.0

SILENCE_S = 6.0
STALL_AFTER_TURNS = 6


@dataclass
class Assessment:
    """Optional LLM-derived inputs; all default to 'nothing detected'."""

    frustration: float = 0.0  # 0..1
    contradiction: bool = False
    safety: bool = False
    slots: dict[str, str] = field(default_factory=dict)


@dataclass
class DriftResult:
    state: State
    score: float
    signals: list[str]
    evidence: list[str]
    changed: bool


def _hits(text: str, lexicon) -> str | None:
    low = text.lower()
    for phrase in lexicon:
        if phrase.lower() in low:
            return phrase
    return None


def is_question(text: str) -> bool:
    return any(q in text for q in QUESTION_MARKS)


def _is_repeat(turn: Turn, previous: list[Turn]) -> bool:
    if not is_question(turn.text):
        return False
    for old in previous:
        if old is turn:
            continue
        ratio = difflib.SequenceMatcher(None, old.text, turn.text).ratio()
        if ratio >= 0.75:
            return True
    return False


class DriftEngine:
    def __init__(self, memory: ConversationMemory) -> None:
        self.memory = memory
        self.state = State.PASSIVE_MONITOR
        self.score = 0.0
        self.signals: list[str] = []
        self.evidence: list[str] = []
        self._offer_turn: int | None = None

    # ── main entry ────────────────────────────────────────────────────────

    def evaluate(self, assessment: Assessment | None = None) -> DriftResult:
        """Recompute the ladder from the current window. Idempotent per turn."""
        a = assessment or Assessment()
        prev_state = self.state
        signals: list[str] = []
        evidence: list[str] = []
        score = 0.0
        window = self.memory.window(WINDOW)

        # hard trigger: safety, from any state
        safety_turn = next(
            (t for t in window if _hits(t.text, SAFETY)), None
        )
        if safety_turn is not None or a.safety:
            self.state = State.SAFETY_ESCALATION
            self.score = 99.0
            self.signals = ["SAFETY"]
            self.evidence = [
                f"safety keyword in {safety_turn.role}: {safety_turn.text!r}"
                if safety_turn
                else "classifier flagged safety"
            ]
            return DriftResult(self.state, self.score, self.signals, self.evidence, True)

        # hard trigger: explicit ask for the agent
        wake_turn = next((t for t in window if _hits(t.text, WAKE)), None)

        # counted signals
        confusion_hits = [t for t in window if _hits(t.text, CONFUSION)]
        if confusion_hits:
            n = min(len(confusion_hits), CAPS["CONFUSION"])
            score += WEIGHTS["CONFUSION"] * n
            signals.append("CONFUSION")
            evidence += [f"{t.role}: {t.text!r}" for t in confusion_hits[-n:]]

        repeats = 0
        for turn in window:
            if _is_repeat(turn, self.memory.by_role(turn.role, 4)):
                repeats += 1
                evidence.append(f"{turn.role} repeated: {turn.text!r}")
        if repeats:
            score += WEIGHTS["REPEATED_QUESTION"] * min(repeats, CAPS["REPEATED_QUESTION"])
            signals.append("REPEATED_QUESTION")

        if a.contradiction:
            score += WEIGHTS["CONTRADICTION"]
            signals.append("CONTRADICTION")

        if a.frustration > 0:
            score += WEIGHTS["FRUSTRATION"] * min(max(a.frustration, 0.0), 1.0)
            signals.append("FRUSTRATION")
            evidence.append(f"frustration={a.frustration:.2f}")

        if self._silence_after_question(window):
            score += WEIGHTS["SILENCE_AFTER_QUESTION"]
            signals.append("SILENCE_AFTER_QUESTION")

        human_turns = [t for t in self.memory.turns if t.role != "agent"]
        if len(human_turns) >= STALL_AFTER_TURNS and self.memory.slots.missing_required:
            score += WEIGHTS["SLOTS_STALLED"]
            signals.append("SLOTS_STALLED")
            evidence.append(
                "unfilled after "
                f"{len(human_turns)} turns: {', '.join(self.memory.slots.missing_required)}"
            )

        if self.memory.languages_differ:
            score += WEIGHTS["LANG_MISMATCH"]
            signals.append("LANG_MISMATCH")

        self.score = round(score, 2)
        self.signals = signals
        self.evidence = evidence[:6]

        self._advance(score, wake_turn)
        return DriftResult(
            self.state, self.score, self.signals, self.evidence, self.state != prev_state
        )

    # ── ladder transitions ────────────────────────────────────────────────

    def _advance(self, score: float, wake_turn: Turn | None) -> None:
        # terminal-ish states are driven explicitly, not by score
        if self.state in (State.ACTIVE_MEDIATION, State.SAFETY_ESCALATION):
            return

        if wake_turn is not None:
            self.state = State.ACTIVE_MEDIATION
            self.evidence.insert(0, f"wake phrase: {wake_turn.text!r}")
            return

        if self.state == State.OFFER_HELP:
            return  # waiting for a reply to the offer

        if score >= OFFER_AT:
            self.state = State.OFFER_HELP
        elif score >= WATCH_AT:
            self.state = State.WATCH
        else:
            self.state = State.PASSIVE_MONITOR

    def accept_offer(self) -> None:
        self.state = State.ACTIVE_MEDIATION

    def decline_offer(self) -> None:
        self.state = State.WATCH

    def resolve(self) -> None:
        self.state = State.RESOLVED

    def back_to_monitor(self) -> None:
        self.state = State.PASSIVE_MONITOR
        self.score = 0.0
        self.signals = []
        self.evidence = []

    # ── helpers ───────────────────────────────────────────────────────────

    def _silence_after_question(self, window: list[Turn]) -> bool:
        if len(window) < 2:
            return False
        last = window[-1]
        if not is_question(last.text):
            return False
        # a question with no answer yet, older than SILENCE_S
        newest_t = max(t.t for t in self.memory.turns) if self.memory.turns else 0.0
        return (newest_t - last.t) >= SILENCE_S


AFFIRMATIVE = ("हाँ", "हां", "जी", "ठीक", "ಹೌದು", "ಸರಿ", "ಆಯ್ತು", "yes", "ok", "haan", "sari")
NEGATIVE = ("नहीं", "ना", "ಬೇಡ", "ಇಲ್ಲ", "no", "nahi", "beda")


def offer_reply(text: str) -> str | None:
    """Classify a reply to the agent's offer: 'yes' | 'no' | None.

    Negation is checked first: "नहीं, ठीक है" ("no, it's fine") is a decline
    even though it contains an affirmative word.
    """
    if _hits(text, NEGATIVE):
        return "no"
    if _hits(text, AFFIRMATIVE):
        return "yes"
    return None
