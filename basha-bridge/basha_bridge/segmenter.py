BOUNDARY_CHARS = "।.?!,"

# Spoken digit words (hi/kn) — a segment must never end mid-number-run,
# or OTPs get split across translation calls.
NUMBER_WORDS = {
    "शून्य", "एक", "दो", "तीन", "चार", "पांच", "पाँच", "छह", "सात", "आठ", "नौ",
    "ಸೊನ್ನೆ", "ಒಂದು", "ಎರಡು", "ಮೂರು", "ನಾಲ್ಕು", "ಐದು", "ಆರು", "ಏಳು", "ಎಂಟು", "ಒಂಬತ್ತು",
}


def _ends_mid_number_run(segment_words: list[str]) -> bool:
    return bool(segment_words) and segment_words[-1].strip("".join(BOUNDARY_CHARS)) in NUMBER_WORDS


def _charlen(words: list[str]) -> int:
    return sum(len(w.strip(BOUNDARY_CHARS)) for w in words)


class Segmenter:
    """Turns a stream of growing partial transcripts into committed segments.

    Policy (from partials spike): the prefix is append-mostly stable, only the
    trailing words flicker. So: hold back the last `hold_words` words, commit
    up to the last clause boundary in what remains, or force-commit once the
    pending run exceeds `max_pending_words`."""

    # Realtime partials carry no punctuation (it appears only in the final),
    # so the word-count force-commit is the primary mechanism, not the fallback.
    # Progress is tracked in characters, not words: the final transcript may
    # retokenize partial words ("ओ टी पी" -> "ओटीपी"), so word counts misalign.
    def __init__(self, hold_words: int = 2, max_pending_words: int = 5):
        self.hold_words = hold_words
        self.max_pending_words = max_pending_words
        self.committed_chars = 0

    def _uncommitted_start(self, words: list[str]) -> int:
        acc = 0
        for i, w in enumerate(words):
            if acc >= self.committed_chars:
                return i
            acc += len(w.strip(BOUNDARY_CHARS))
        return len(words)

    def feed(self, partial_text: str) -> list[str]:
        words = partial_text.split()
        stable = words[: -self.hold_words] if len(words) > self.hold_words else []
        start = self._uncommitted_start(stable)
        pending = stable[start:]
        if not pending:
            return []

        boundary_idx = None
        for i in range(len(stable) - 1, start - 1, -1):
            if stable[i].rstrip()[-1:] in BOUNDARY_CHARS:
                boundary_idx = i
                break

        if boundary_idx is not None:
            segment_words = stable[start : boundary_idx + 1]
        elif len(pending) >= self.max_pending_words:
            segment_words = pending
        else:
            return []

        if _ends_mid_number_run(segment_words):
            return []
        # Look ahead: if the word right after the segment is a number word
        # (e.g. "gate | two"), defer so quantity phrases stay whole.
        end = start + len(segment_words)
        if end < len(words) and words[end].strip(BOUNDARY_CHARS) in NUMBER_WORDS:
            return []

        self.committed_chars += _charlen(segment_words)
        return [" ".join(segment_words)]

    def flush(self, final_text: str) -> list[str]:
        """Commit whatever remains once the final transcript arrives."""
        words = final_text.split()
        start = self._uncommitted_start(words)
        remainder = words[start:]
        self.committed_chars += _charlen(remainder)
        return [" ".join(remainder)] if remainder else []
