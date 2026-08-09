"""Drift engine + ladder tests — pure logic, no network. Run: uv run pytest -q"""

from basha_bridge.conversation import ConversationMemory, TaskSlots, Turn
from basha_bridge.drift import Assessment, DriftEngine, State, offer_reply
from basha_bridge.mediator import TurnManager, allowed_actions


def feed(engine: ConversationMemory | DriftEngine, *turns, assessment=None):
    for role, lang, t, text in turns:
        engine.memory.add(Turn(role=role, text=text, lang=lang, t=t))
        result = engine.evaluate(assessment)
    return result


def new_engine() -> DriftEngine:
    return DriftEngine(ConversationMemory())


# ── product rule: language mismatch alone is NOT drift ─────────────────────


def test_language_mismatch_alone_stays_passive():
    e = new_engine()
    r = feed(
        e,
        ("rider", "hi-IN", 0.0, "मैं गेट पर हूँ"),
        ("driver", "kn-IN", 3.0, "ಸರಿ ಬರ್ತೀನಿ"),
        ("rider", "hi-IN", 6.0, "ठीक है"),
    )
    assert r.state == State.PASSIVE_MONITOR
    assert "LANG_MISMATCH" in r.signals
    assert r.score < 2.0


def test_same_language_smooth_chat_never_escalates():
    e = new_engine()
    r = feed(
        e,
        ("rider", "hi-IN", 0.0, "मैं गेट दो पर हूँ"),
        ("driver", "hi-IN", 3.0, "ठीक है, दो मिनट"),
        ("rider", "hi-IN", 6.0, "चाय की दुकान के पास"),
        ("driver", "hi-IN", 9.0, "हाँ आ रहा हूँ"),
    )
    assert r.state == State.PASSIVE_MONITOR


# ── escalation ────────────────────────────────────────────────────────────


def test_confusion_plus_mismatch_reaches_watch():
    e = new_engine()
    r = feed(
        e,
        ("rider", "hi-IN", 0.0, "आप कहाँ हैं?"),
        ("driver", "kn-IN", 3.0, "ಏನು? ಅರ್ಥ ಆಗಲಿಲ್ಲ."),
    )
    assert r.state == State.WATCH
    assert "CONFUSION" in r.signals


def test_repeated_confusion_reaches_offer_help():
    e = new_engine()
    r = feed(
        e,
        ("rider", "hi-IN", 0.0, "आप कहाँ हैं?"),
        ("driver", "kn-IN", 3.0, "ಏನು? ಅರ್ಥ ಆಗಲಿಲ್ಲ."),
        ("rider", "hi-IN", 6.0, "आप कहाँ हैं?"),
        ("driver", "kn-IN", 9.0, "ಕ್ಷಮಿಸಿ, ಗೊತ್ತಾಗಲಿಲ್ಲ."),
    )
    assert r.state == State.OFFER_HELP
    assert r.score >= 4.0


def test_safety_escalates_from_any_state():
    e = new_engine()
    feed(e, ("rider", "hi-IN", 0.0, "आप कहाँ हैं?"))
    r = feed(e, ("rider", "hi-IN", 3.0, "एक्सीडेंट हो गया, पुलिस बुलाओ!"))
    assert r.state == State.SAFETY_ESCALATION


def test_safety_wins_over_active_mediation():
    e = new_engine()
    e.state = State.ACTIVE_MEDIATION
    r = feed(e, ("driver", "kn-IN", 0.0, "ಅಪಘಾತ ಆಗಿದೆ!"))
    assert r.state == State.SAFETY_ESCALATION


def test_wake_phrase_jumps_straight_to_mediation():
    e = new_engine()
    r = feed(e, ("rider", "hi-IN", 0.0, "एजेंट, मदद करो"))
    assert r.state == State.ACTIVE_MEDIATION


def test_llm_frustration_contributes_to_score():
    e = new_engine()
    r = feed(
        e,
        ("rider", "hi-IN", 0.0, "आप कहाँ हैं?"),
        ("driver", "kn-IN", 3.0, "ಬರ್ತಾ ಇದ್ದೀನಿ"),
        assessment=Assessment(frustration=1.0, contradiction=True),
    )
    assert "FRUSTRATION" in r.signals and "CONTRADICTION" in r.signals
    assert r.state in (State.WATCH, State.OFFER_HELP)


def test_offer_help_waits_for_a_reply():
    """Once offered, the ladder must not slide back on its own."""
    e = new_engine()
    e.state = State.OFFER_HELP
    r = feed(e, ("rider", "hi-IN", 0.0, "अच्छा"))
    assert r.state == State.OFFER_HELP


def test_offer_accept_and_decline():
    e = new_engine()
    e.state = State.OFFER_HELP
    e.accept_offer()
    assert e.state == State.ACTIVE_MEDIATION
    e2 = new_engine()
    e2.state = State.OFFER_HELP
    e2.decline_offer()
    assert e2.state == State.WATCH


def test_offer_reply_classification():
    assert offer_reply("हाँ ठीक है") == "yes"
    assert offer_reply("ಸರಿ") == "yes"
    assert offer_reply("नहीं, ठीक है") == "no"
    assert offer_reply("मैं गेट पर हूँ") is None


# ── slots + allowed actions ───────────────────────────────────────────────


def test_slots_update_ignores_empty_and_unknown():
    s = TaskSlots()
    changed = s.update({"pickup_point": "गेट दो", "eta": "", "bogus": "x", "otp": None})
    assert changed == ["pickup_point"]
    assert s.values == {"pickup_point": "गेट दो"}
    assert not s.complete


def test_allowed_actions_narrow_as_slots_fill():
    m = ConversationMemory()
    assert "ASK_RIDER_PICKUP_POINT" in allowed_actions(m)
    m.slots.update(
        {
            "pickup_point": "गेट दो",
            "driver_location": "मैजेस्टिक",
            "eta": "5 min",
            "agreed_next_action": "wait at gate two",
        }
    )
    assert m.slots.complete
    assert allowed_actions(m) == [
        "SUMMARIZE_AND_EXIT",
        "CONFIRM_WITH_RIDER",
        "CONFIRM_WITH_DRIVER",
    ]


# ── turn manager ──────────────────────────────────────────────────────────


def test_turn_manager_waits_for_the_addressed_party():
    from basha_bridge.mediator import MediationAction

    tm = TurnManager(patience=2)
    assert tm.should_act("rider")  # nothing pending
    tm.record(MediationAction("ASK_DRIVER_ETA", "driver", "?", ""))
    assert not tm.should_act("rider")  # wrong party answered
    assert tm.should_act("driver")  # addressed party answered


def test_turn_manager_gives_up_after_patience():
    from basha_bridge.mediator import MediationAction

    tm = TurnManager(patience=2)
    tm.record(MediationAction("ASK_DRIVER_ETA", "driver", "?", ""))
    assert not tm.should_act("rider")
    assert tm.should_act("rider")  # silent driver — move on rather than stall


def test_rephrased_question_counts_as_a_repeat():
    """Real speech rephrases; caught live where a verbatim matcher missed it."""
    e = new_engine()
    r = feed(
        e,
        ("rider", "hi-IN", 0.0, "भैया आप कहाँ हैं? मैं यहाँ खड़ा हूँ।"),
        ("rider", "hi-IN", 5.0, "अरे भैया आप कहाँ हैं बताइए ना"),
    )
    assert "REPEATED_QUESTION" in r.signals


def test_different_questions_are_not_repeats():
    e = new_engine()
    r = feed(
        e,
        ("rider", "hi-IN", 0.0, "आपका ओटीपी क्या है?"),
        ("rider", "hi-IN", 5.0, "गाड़ी का रंग कौन सा है?"),
    )
    assert "REPEATED_QUESTION" not in r.signals


def test_where_question_is_not_an_acceptance():
    """"कहाँ" (where) contains "हाँ" (yes) — substring matching read a question
    as accepting the agent's offer."""
    assert offer_reply("आप कहाँ हैं?") is None
    assert offer_reply("गेट के पास। आप कहाँ हैं?") is None
    assert offer_reply("हाँ ठीक है") == "yes"
