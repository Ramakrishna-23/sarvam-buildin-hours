"""Incremental clause chunker.

Turns cumulative `transcript.partial` texts from saaras:v3-realtime into
append-only committed segments that are safe to translate + speak immediately.
Committed audio can never be retracted, so we only commit text that is either
structurally closed (clause boundary), stable across consecutive partials,
or older than the max-lag ceiling.
"""

from __future__ import annotations

import difflib
import re
import time
from dataclasses import dataclass
from typing import Callable

# Clause-closing punctuation across hi/kn (danda, western, question/exclaim).
_BOUNDARY_RE = re.compile(r"[।॥.?!,;]")
# Connectives that open a new clause — safe to commit everything before them.
_CONNECTIVES = {
    "तो", "फिर", "लेकिन", "पर", "और", "क्योंकि", "अगर", "मगर", "इसलिए",
    "ಮತ್ತು", "ಆದರೆ", "ಆಮೇಲೆ", "ಅಂದರೆ", "ಯಾಕಂದ್ರೆ", "ಆದ್ರೂ", "ಅದಕ್ಕೆ",
}
_DIGIT_RE = re.compile(r"[0-9०-९೦-೯]")
# Spelled-out digits (how people actually say OTPs) — hold these like numerals.
_DIGIT_WORDS = {
    "शून्य", "जीरो", "एक", "दो", "तीन", "चार", "पांच", "पाँच", "छह", "छे",
    "सात", "आठ", "नौ",
    "ಸೊನ್ನೆ", "ಒಂದು", "ಎರಡು", "ಮೂರು", "ನಾಲ್ಕು", "ಐದು", "ಆರು", "ಏಳು",
    "ಎಂಟು", "ಒಂಬತ್ತು",
}


def _ends_in_digit_run(text: str) -> bool:
    tail = text.rstrip()
    if not tail:
        return False
    if _DIGIT_RE.match(tail[-1]):
        return True
    words = tail.split()
    return bool(words) and words[-1] in _DIGIT_WORDS


def _map_offset(src: str, dst: str, pos: int) -> int:
    """Map a character offset in `src` to the corresponding offset in `dst`
    via sequence alignment (tolerates finalizer rewrites of earlier text)."""
    sm = difflib.SequenceMatcher(None, src, dst, autojunk=False)
    mapped = 0
    for a, b, size in sm.get_matching_blocks():
        if a >= pos:
            break
        mapped = b + min(size, pos - a)
    return min(mapped, len(dst))


@dataclass
class Segment:
    utt_idx: int
    seq: int
    text: str
    reason: str  # boundary | stable | maxlag | final
    t_first_pending: float  # when the oldest word of this segment first appeared
    t_committed: float


class IncrementalChunker:
    def __init__(
        self,
        *,
        max_lag_s: float = 1.2,
        agreement_ticks: int = 2,
        min_words: int = 3,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_lag_s = max_lag_s
        self.agreement_ticks = agreement_ticks
        self.min_words = min_words
        self._clock = clock
        self._seq = 0
        self._reset(utt_idx=-1)

    def _reset(self, utt_idx: int) -> None:
        self._utt_idx = utt_idx
        self._committed_len = 0
        self._last_text = ""
        self._stable_ticks = 0
        self._pending_since: float | None = None

    # ── event entry points ────────────────────────────────────────────────

    def on_partial(self, utt_idx: int, text: str) -> list[Segment]:
        now = self._clock()
        if utt_idx != self._utt_idx:
            self._reset(utt_idx)
        if len(text) < self._committed_len:
            # finalizer regression below what we already spoke; wait for more
            self._last_text = text
            return []

        if text == self._last_text:
            self._stable_ticks += 1
        else:
            self._stable_ticks = 1
            self._last_text = text

        delta = text[self._committed_len :]
        if not delta.strip():
            return []
        if self._pending_since is None:
            self._pending_since = now

        cut = self._boundary_cut(delta)
        if cut:
            return self._commit(text, self._committed_len + cut, "boundary", now)

        if (
            self._stable_ticks >= self.agreement_ticks
            and len(delta.split()) >= self.min_words
            and not _ends_in_digit_run(delta)
        ):
            return self._commit(text, len(text), "stable", now)
        return []

    def on_final(self, utt_idx: int, text: str) -> list[Segment]:
        now = self._clock()
        if utt_idx != self._utt_idx:
            self._reset(utt_idx)
        # The finalizer may rewrite already-committed text ("दो"→"2", added
        # punctuation), so map the committed boundary into the final string by
        # alignment instead of trusting raw character offsets.
        if self._committed_len:
            base = _map_offset(self._last_text, text, self._committed_len)
            # never start the remainder mid-word (alignment can land inside a
            # word the finalizer respelled, e.g. "खड़े"→"खड़ी")
            if 0 < base < len(text) and not text[base - 1].isspace():
                nxt = text.find(" ", base)
                base = nxt + 1 if nxt != -1 else len(text)
        else:
            base = 0
        remainder = text[base:]
        out: list[Segment] = []
        if remainder.strip():
            if self._pending_since is None:
                self._pending_since = now
            self._committed_len = base
            out = self._commit(text, len(text), "final", now)
        self._reset(utt_idx=-1)
        return out

    def poll(self) -> list[Segment]:
        """Call every ~100 ms; enforces the max-lag ceiling."""
        now = self._clock()
        if self._pending_since is None:
            return []
        delta = self._last_text[self._committed_len :]
        if not delta.strip():
            return []
        age = now - self._pending_since
        if age < self.max_lag_s:
            return []
        # give an in-flight digit run (OTP) extra time to complete
        if _ends_in_digit_run(delta) and age < 2 * self.max_lag_s:
            return []
        return self._commit(self._last_text, len(self._last_text), "maxlag", now)

    # ── internals ─────────────────────────────────────────────────────────

    def _boundary_cut(self, delta: str) -> int:
        """Rightmost safe cut position within delta, or 0 if none."""
        best = 0
        for m in _BOUNDARY_RE.finditer(delta):
            end = m.end()
            candidate, rest = delta[:end], delta[end:]
            if _ends_in_digit_run(candidate) and _DIGIT_RE.search(rest[:2] or ""):
                continue  # punctuation inside a digit run (e.g. "4, 7, 2")
            best = end
        if best:
            return best
        words = delta.split()
        for i in range(len(words) - 1, self.min_words - 1, -1):
            if words[i] in _CONNECTIVES:
                pos = delta.find(words[i])
                if pos > 0:
                    return pos
        return 0

    def _commit(self, full_text: str, upto: int, reason: str, now: float) -> list[Segment]:
        piece = full_text[self._committed_len : upto].strip()
        if not piece:
            return []
        self._seq += 1
        seg = Segment(
            utt_idx=self._utt_idx,
            seq=self._seq,
            text=piece,
            reason=reason,
            t_first_pending=self._pending_since or now,
            t_committed=now,
        )
        self._committed_len = upto
        rest = full_text[upto:]
        self._pending_since = now if rest.strip() else None
        self._stable_ticks = 0
        return [seg]
