"""Replay harness: drive the drift engine from a scripted transcript.

No audio, no WebRTC — this is how Phase 5/6 stay testable offline.

    uv run python -m basha_bridge.replay scenarios/pickup_fail.json
    uv run python -m basha_bridge.replay scenarios/pickup_fail.json --llm
    uv run python -m basha_bridge.replay scenarios/pickup_fail.json --llm --mediate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .conversation import ConversationMemory, Turn
from .drift import Assessment, DriftEngine, State, offer_reply
from .mediator import TurnManager, assess, next_action, summary_text

DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

STATE_COLOR = {
    State.PASSIVE_MONITOR: DIM,
    State.WATCH: YELLOW,
    State.OFFER_HELP: CYAN,
    State.ACTIVE_MEDIATION: BOLD + CYAN,
    State.SAFETY_ESCALATION: RED,
    State.RESOLVED: GREEN,
}


async def replay(path: Path, use_llm: bool, mediate: bool) -> int:
    scenario = json.loads(path.read_text())
    memory = ConversationMemory()
    engine = DriftEngine(memory)
    client = None
    if use_llm:
        from sarvamai import AsyncSarvamAI

        from . import config

        client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)

    print(f"\n{BOLD}{scenario['name']}{RESET} — {scenario.get('description','')}")
    print(f"{DIM}mode: {'llm+rules' if use_llm else 'rules only'}"
          f"{' · mediation on' if mediate else ''}{RESET}\n")

    visited: list[str] = [engine.state.value]
    agent_lines: list[str] = []
    turns_mgr = TurnManager()

    for raw in scenario["turns"]:
        turn = Turn(
            role=raw["role"],
            text=raw["text"],
            lang=raw.get("lang"),
            t=float(raw.get("t", 0.0)),
        )

        # a reply to the agent's offer decides whether mediation starts
        if engine.state == State.OFFER_HELP:
            reply = offer_reply(turn.text)
            if reply == "yes":
                engine.accept_offer()
                visited.append(engine.state.value)
            elif reply == "no":
                engine.decline_offer()
                visited.append(engine.state.value)

        memory.add(turn)
        print(f"  {turn.role:<7} {DIM}[{turn.lang or '?'}]{RESET} {turn.text}")

        assessment = Assessment()
        if client is not None:
            assessment = await assess(client, memory)
            if assessment.slots:
                changed = memory.slots.update(assessment.slots)
                if changed:
                    print(f"          {GREEN}slots+{RESET} "
                          f"{', '.join(f'{k}={memory.slots.values[k]!r}' for k in changed)}")

        result = engine.evaluate(assessment)
        colour = STATE_COLOR.get(result.state, "")
        marker = "→" if result.changed else " "
        print(f"        {marker} {colour}{result.state.value}{RESET} "
              f"{DIM}score={result.score} {'·'.join(result.signals) or '-'}{RESET}")
        for ev in result.evidence[:2]:
            print(f"          {DIM}· {ev}{RESET}")
        if result.changed:
            visited.append(result.state.value)

        # Phase 6: the agent actually says something
        if (
            mediate
            and client is not None
            and engine.state == State.ACTIVE_MEDIATION
            and turns_mgr.should_act(turn.role)
        ):
            action = await next_action(client, memory)
            turns_mgr.record(action)
            line = f"[{action.action} → {action.target}] {action.utterance}"
            agent_lines.append(line)
            print(f"  {BOLD}{CYAN}agent{RESET}   {line}  {DIM}({action.reason}){RESET}")
            memory.add(Turn(role="agent", text=action.utterance))
            if action.action == "SUMMARIZE_AND_EXIT":
                engine.resolve()
                visited.append(State.RESOLVED.value)
                print(f"        → {GREEN}{State.RESOLVED.value}{RESET} "
                      f"{DIM}{summary_text(memory)}{RESET}")
                break
        elif engine.state == State.OFFER_HELP and not agent_lines:
            print(f"  {BOLD}{CYAN}agent{RESET}   "
                  f"[OFFER_HELP → both] I can help sort out the pickup. Shall I?")
            agent_lines.append("offer")

    # slots complete but the script ran out of turns — the agent should still exit
    if (
        mediate
        and client is not None
        and engine.state == State.ACTIVE_MEDIATION
        and memory.slots.complete
    ):
        print(f"  {BOLD}{CYAN}agent{RESET}   "
              f"[SUMMARIZE_AND_EXIT → both] {summary_text(memory)}")
        engine.resolve()
        visited.append(State.RESOLVED.value)
        print(f"        → {GREEN}{State.RESOLVED.value}{RESET}")

    print(f"\n  {BOLD}final:{RESET} {engine.state.value}   "
          f"{BOLD}path:{RESET} {' → '.join(dict.fromkeys(visited))}")
    if memory.slots.values:
        print(f"  {BOLD}slots:{RESET} "
              f"{json.dumps(memory.slots.values, ensure_ascii=False)}")
        print(f"  {BOLD}missing:{RESET} {', '.join(memory.slots.missing_required) or 'none'}")

    expect = scenario.get("expect", {})
    failures = []
    if "final_state" in expect and engine.state.value != expect["final_state"]:
        failures.append(f"expected final {expect['final_state']}, got {engine.state.value}")
    for required in expect.get("reaches", []):
        if required not in visited:
            failures.append(f"never reached {required}")
    for forbidden in expect.get("never", []):
        if forbidden in visited:
            failures.append(f"should never have reached {forbidden}")

    if not expect:
        print(f"\n  {DIM}(no assertions in scenario){RESET}\n")
        return 0
    if failures:
        print(f"\n  {RED}✘ FAIL{RESET}")
        for f in failures:
            print(f"    - {f}")
        print()
        return 1
    print(f"\n  {GREEN}✔ PASS{RESET}\n")
    return 0


async def stream_replay(path: Path, use_llm: bool, mediate: bool):
    """Async generator of bus events for the dashboard (mirrors replay())."""
    scenario = json.loads(path.read_text())
    memory = ConversationMemory()
    engine = DriftEngine(memory)
    turns_mgr = TurnManager()
    client = None
    if use_llm:
        from sarvamai import AsyncSarvamAI

        from . import config

        client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)

    yield {
        "tag": "scenario.start",
        "name": scenario["name"],
        "description": scenario.get("description", ""),
        "mode": "llm+rules" if use_llm else "rules only",
        "turns": len(scenario["turns"]),
    }

    visited = [engine.state.value]
    for raw in scenario["turns"]:
        turn = Turn(
            role=raw["role"], text=raw["text"], lang=raw.get("lang"), t=float(raw.get("t", 0.0))
        )
        if engine.state == State.OFFER_HELP:
            reply = offer_reply(turn.text)
            if reply == "yes":
                engine.accept_offer()
                visited.append(engine.state.value)
            elif reply == "no":
                engine.decline_offer()

        memory.add(turn)
        yield {
            "tag": "utterance.final",
            "speaker": turn.role,
            "text": turn.text,
            "lang": turn.lang,
            "utt": len(memory.turns),
        }

        assessment = Assessment()
        if client is not None:
            assessment = await assess(client, memory)
            changed = memory.slots.update(assessment.slots)
            if changed:
                yield {
                    "tag": "slots.updated",
                    "changed": changed,
                    "slots": memory.slots.values,
                    "missing": memory.slots.missing_required,
                }

        result = engine.evaluate(assessment)
        if result.changed:
            visited.append(result.state.value)
        yield {
            "tag": "agent.state",
            "state": result.state.value,
            "score": result.score,
            "signals": result.signals,
            "evidence": result.evidence[:3],
            "changed": result.changed,
        }

        if (
            mediate
            and client is not None
            and engine.state == State.ACTIVE_MEDIATION
            and turns_mgr.should_act(turn.role)
        ):
            action = await next_action(client, memory)
            turns_mgr.record(action)
            yield {
                "tag": "mediation.action",
                "action": action.action,
                "target": action.target,
                "utterance": action.utterance,
                "reason": action.reason,
            }
            memory.add(Turn(role="agent", text=action.utterance))
            if action.action == "SUMMARIZE_AND_EXIT":
                engine.resolve()
                visited.append(State.RESOLVED.value)
                break

    if mediate and client is not None and engine.state == State.ACTIVE_MEDIATION and memory.slots.complete:
        yield {
            "tag": "mediation.action",
            "action": "SUMMARIZE_AND_EXIT",
            "target": "both",
            "utterance": summary_text(memory),
            "reason": "all required slots filled",
        }
        engine.resolve()
        visited.append(State.RESOLVED.value)

    expect = scenario.get("expect", {})
    failures = []
    if "final_state" in expect and engine.state.value != expect["final_state"]:
        failures.append(f"expected final {expect['final_state']}, got {engine.state.value}")
    for required in expect.get("reaches", []):
        if required not in visited:
            failures.append(f"never reached {required}")
    for forbidden in expect.get("never", []):
        if forbidden in visited:
            failures.append(f"should never have reached {forbidden}")

    yield {
        "tag": "scenario.complete",
        "final_state": engine.state.value,
        "path": list(dict.fromkeys(visited)),
        "slots": memory.slots.values,
        "missing": memory.slots.missing_required,
        "passed": not failures and bool(expect),
        "asserted": bool(expect),
        "failures": failures,
    }


def main() -> None:
    p = argparse.ArgumentParser(prog="basha-bridge replay")
    p.add_argument("scenario", type=Path, nargs="+")
    p.add_argument("--llm", action="store_true", help="use sarvam-105b for assessment")
    p.add_argument("--mediate", action="store_true", help="run Phase 6 mediation turns")
    a = p.parse_args()
    rc = 0
    for path in a.scenario:
        rc |= asyncio.run(replay(path, a.llm, a.mediate))
    sys.exit(rc)


if __name__ == "__main__":
    main()
