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
    parser.add_argument(
        "--check", action="store_true", help="validate Sarvam key and LiveKit join"
    )
    args = parser.parse_args()

    if args.check:
        sys.exit(asyncio.run(run_checks()))
    parser.print_help()


if __name__ == "__main__":
    main()
