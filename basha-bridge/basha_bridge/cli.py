import argparse
import asyncio
import sys

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}✔{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"{RED}✘{RESET} {msg}")


async def check_sarvam() -> bool:
    from sarvamai import AsyncSarvamAI

    from . import config

    if not config.SARVAM_API_KEY:
        fail("SARVAM_API_KEY not set (copy .env.example to .env)")
        return False
    try:
        client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)
        resp = await client.text.translate(
            input="Hello",
            source_language_code="en-IN",
            target_language_code="hi-IN",
        )
        ok(f"Sarvam API key valid (translate: {resp.translated_text!r})")
        return True
    except Exception as e:
        fail(f"Sarvam check failed: {e}")
        return False


async def check_livekit() -> bool:
    from livekit import api, rtc

    from . import config

    missing = [
        name
        for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
        if not getattr(config, name)
    ]
    if missing:
        fail(f"missing env: {', '.join(missing)}")
        return False
    try:
        token = (
            api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
            .with_identity("check-agent")
            .with_grants(api.VideoGrants(room_join=True, room="basha-check"))
            .to_jwt()
        )
        room = rtc.Room()
        await room.connect(config.LIVEKIT_URL, token)
        ok(f"LiveKit join OK (room: {room.name})")
        await room.disconnect()
        return True
    except Exception as e:
        fail(f"LiveKit check failed: {e}")
        return False


async def run_checks() -> int:
    results = [await check_sarvam(), await check_livekit()]
    return 0 if all(results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="basha-bridge")
    parser.add_argument("--check", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("check", help="validate Sarvam key and LiveKit join")

    p_agent = sub.add_parser("agent", help="run the translation agent in a room")
    p_agent.add_argument("--room", default="demo")

    p_serve = sub.add_parser("serve", help="serve the demo frontend + token API")
    p_serve.add_argument("--port", type=int, default=8080)

    p_sim = sub.add_parser("simulate", help="join a room as a fake participant")
    p_sim.add_argument("--room", default="demo")
    p_sim.add_argument("--id", default="driver")
    p_sim.add_argument("--fixture", default="kn_pickup.wav")
    p_sim.add_argument("--delay", type=float, default=0.0)
    p_sim.add_argument("--repeat", type=int, default=1)
    p_sim.add_argument("--record", default=None)

    p_replay = sub.add_parser("replay", help="replay drift scenarios offline")
    p_replay.add_argument("scenario", nargs="+")
    p_replay.add_argument("--llm", action="store_true", help="use sarvam-105b")
    p_replay.add_argument("--mediate", action="store_true", help="run mediation turns")

    args = parser.parse_args()

    if args.check or args.cmd == "check":
        sys.exit(asyncio.run(run_checks()))
    elif args.cmd == "agent":
        from .agent import run_agent

        asyncio.run(run_agent(args.room))
    elif args.cmd == "serve":
        from .server import run_server

        run_server(args.port)
    elif args.cmd == "simulate":
        from .simulate import run_simulation

        sys.exit(
            asyncio.run(
                run_simulation(
                    args.room, args.id, args.fixture, args.delay, args.repeat, args.record
                )
            )
        )
    elif args.cmd == "replay":
        from pathlib import Path

        from .replay import replay

        rc = 0
        for name in args.scenario:
            rc |= asyncio.run(replay(Path(name), args.llm, args.mediate))
        sys.exit(rc)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
