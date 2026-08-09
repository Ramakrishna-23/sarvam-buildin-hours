"""Chunker unit tests — pure logic, no network. Run: uv run pytest -q"""

from basha_bridge.chunker import IncrementalChunker, Segment


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make(clock, **kw):
    return IncrementalChunker(clock=clock, **kw)


def texts(segs: list[Segment]) -> list[str]:
    return [s.text for s in segs]


def test_boundary_commit_mid_utterance():
    clock = FakeClock()
    c = make(clock)
    assert c.on_partial(0, "ನಾನು ಮೆಜೆಸ್ಟಿಕ್") == []
    segs = c.on_partial(0, "ನಾನು ಮೆಜೆಸ್ಟಿಕ್ ಹತ್ತಿರ ಇದ್ದೀನಿ. ಗೇಟ್")
    assert texts(segs) == ["ನಾನು ಮೆಜೆಸ್ಟಿಕ್ ಹತ್ತಿರ ಇದ್ದೀನಿ."]
    assert segs[0].reason == "boundary"


def test_stable_prefix_commit():
    clock = FakeClock()
    c = make(clock, agreement_ticks=2, min_words=3)
    c.on_partial(0, "मैं गेट के पास")
    segs = c.on_partial(0, "मैं गेट के पास")  # identical partial → stable ×2
    assert texts(segs) == ["मैं गेट के पास"]
    assert segs[0].reason == "stable"


def test_final_commits_remainder_only():
    clock = FakeClock()
    c = make(clock)
    c.on_partial(0, "मैं गेट दो के पास हूँ, चाय")
    # boundary committed "मैं गेट दो के पास हूँ,"
    segs = c.on_final(0, "मैं गेट दो के पास हूँ, चाय की दुकान के पास।")
    assert texts(segs) == ["चाय की दुकान के पास।"]
    assert segs[0].reason == "final"


def test_max_lag_forces_commit():
    clock = FakeClock()
    c = make(clock, max_lag_s=1.2)
    c.on_partial(0, "भैया सुनिए ज़रा")
    assert c.poll() == []
    clock.t = 1.3
    segs = c.poll()
    assert texts(segs) == ["भैया सुनिए ज़रा"]
    assert segs[0].reason == "maxlag"


def test_digit_run_is_held_not_split():
    clock = FakeClock()
    c = make(clock, agreement_ticks=2, min_words=2)
    c.on_partial(0, "मेरा ओटीपी 47")
    segs = c.on_partial(0, "मेरा ओटीपी 47")  # stable, but ends in digits → hold
    assert segs == []
    segs = c.on_final(0, "मेरा ओटीपी 4729 है।")
    assert texts(segs) == ["मेरा ओटीपी 4729 है।"]


def test_digit_run_survives_max_lag_grace():
    clock = FakeClock()
    c = make(clock, max_lag_s=1.0)
    c.on_partial(0, "ओटीपी 47")
    clock.t = 1.5  # past max_lag but inside 2× grace for digit tails
    assert c.poll() == []
    clock.t = 2.5  # past 2× grace → give up holding
    assert texts(c.poll()) == ["ओटीपी 47"]


def test_new_utterance_resets_state():
    clock = FakeClock()
    c = make(clock)
    c.on_partial(0, "पहला वाक्य यहाँ।")
    segs = c.on_final(0, "पहला वाक्य यहाँ।")
    c.on_partial(1, "दूसरा वाक्य")
    segs = c.on_final(1, "दूसरा वाक्य पूरा।")
    assert texts(segs) == ["दूसरा वाक्य पूरा।"]


def test_short_utterance_final_only():
    clock = FakeClock()
    c = make(clock)
    segs = c.on_final(3, "हाँ ठीक है।")
    assert texts(segs) == ["हाँ ठीक है।"]


def test_committed_text_is_append_only():
    """Finalizer rewriting already-committed text must not re-emit it."""
    clock = FakeClock()
    c = make(clock)
    c.on_partial(0, "गेट दो पर हूँ,")   # commits on boundary
    segs = c.on_final(0, "गेट 2 पर हूँ, ठीक है।")  # final rewrote prefix
    joined = " ".join(texts(segs))
    assert "गेट दो पर हूँ" not in joined  # not spoken twice


def test_spelled_digit_words_are_held():
    """OTP said as words ("चार सात दो नौ") must not split across segments."""
    clock = FakeClock()
    c = make(clock, agreement_ticks=2, min_words=3)
    c.on_partial(0, "मेरा ओ टी पी चार")
    segs = c.on_partial(0, "मेरा ओ टी पी चार")  # stable but ends on digit word
    assert segs == []
    c.on_partial(0, "मेरा ओ टी पी चार सात दो नौ है")
    segs = c.on_partial(0, "मेरा ओ टी पी चार सात दो नौ है")
    assert texts(segs) == ["मेरा ओ टी पी चार सात दो नौ है"]


def test_final_remainder_snaps_to_word_boundary():
    """Finalizer respelling a committed word must not leave a broken fragment."""
    clock = FakeClock()
    c = make(clock, agreement_ticks=2, min_words=3)
    c.on_partial(0, "मैं मैजेस्टिक के पास खड़े")
    c.on_partial(0, "मैं मैजेस्टिक के पास खड़े")  # stable commit, ends "खड़े"
    segs = c.on_final(0, "मैं मैजेस्टिक के पास खड़ी हूँ।")
    assert texts(segs) == ["हूँ।"]  # not "ी हूँ।"
