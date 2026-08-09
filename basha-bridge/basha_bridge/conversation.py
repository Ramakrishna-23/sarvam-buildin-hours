"""Conversation memory and task slot store.

Shared state for the drift engine and the mediator: a rolling bilingual
transcript plus the pickup-task slots that decide whether the conversation is
actually progressing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Role = Literal["rider", "driver", "agent"]

SLOT_NAMES = (
    "pickup_point",
    "landmark",
    "driver_location",
    "eta",
    "otp",
    "blocker",
    "agreed_next_action",
)

# Slots that must be filled before mediation can exit successfully.
REQUIRED_SLOTS = ("pickup_point", "driver_location", "eta", "agreed_next_action")


@dataclass
class Turn:
    role: Role
    text: str
    lang: str | None = None
    t: float = 0.0
    translated: str | None = None
    is_question: bool = False

    def as_line(self) -> str:
        who = self.role
        lang = f"/{self.lang}" if self.lang else ""
        return f"{who}{lang}: {self.text}"


@dataclass
class TaskSlots:
    values: dict[str, str] = field(default_factory=dict)

    def update(self, incoming: dict[str, str | None]) -> list[str]:
        """Merge non-empty slot values. Returns the names that changed."""
        changed = []
        for name, value in incoming.items():
            if name not in SLOT_NAMES:
                continue
            if not value or not str(value).strip():
                continue
            value = str(value).strip()
            if self.values.get(name) != value:
                self.values[name] = value
                changed.append(name)
        return changed

    @property
    def filled(self) -> list[str]:
        return [n for n in SLOT_NAMES if self.values.get(n)]

    @property
    def missing_required(self) -> list[str]:
        return [n for n in REQUIRED_SLOTS if not self.values.get(n)]

    @property
    def complete(self) -> bool:
        return not self.missing_required


@dataclass
class ConversationMemory:
    """Rolling transcript plus per-speaker language profile."""

    max_turns: int = 40
    turns: list[Turn] = field(default_factory=list)
    slots: TaskSlots = field(default_factory=TaskSlots)
    langs: dict[str, str] = field(default_factory=dict)

    def add(self, turn: Turn) -> None:
        self.turns.append(turn)
        if turn.lang and turn.role != "agent":
            self.langs[turn.role] = turn.lang
        if len(self.turns) > self.max_turns:
            del self.turns[: len(self.turns) - self.max_turns]

    def window(self, n: int) -> list[Turn]:
        return [t for t in self.turns if t.role != "agent"][-n:]

    def by_role(self, role: Role, n: int = 3) -> list[Turn]:
        return [t for t in self.turns if t.role == role][-n:]

    def transcript(self, n: int = 12) -> str:
        return "\n".join(t.as_line() for t in self.turns[-n:])

    @property
    def languages_differ(self) -> bool:
        seen = {v for k, v in self.langs.items() if k in ("rider", "driver")}
        return len(seen) >= 2

    def other(self, role: Role) -> Role:
        return "driver" if role == "rider" else "rider"
