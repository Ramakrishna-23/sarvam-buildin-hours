"""Publish prerecorded fixture WAVs as driver/customer LiveKit participants.

Use this to test Phase 3/4 without laptop microphone/background noise:

    uv run basha-fixture-call --room basha-demo

Open the web UI as observer:

    https://basha-bridge.pages.dev/?role=observer&room=basha-demo

The first two files per side are language-lock utterances for Phase 4. After a
short wait, the script plays relay-test utterances that should be translated by
the agent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from livekit import api, rtc

from . import config

NUM_CHANNELS = 1
FRAME_MS = 20
FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"


@dataclass(frozen=True)
class WavAudio:
    path: Path
    sample_rate: int
    channels: int
    pcm: bytes


class SyntheticParticipant:
    def __init__(self, *, room_name: str, role: str, identity: str) -> None:
        self.room_name = room_name
        self.role = role
        self.identity = identity
        self.room = rtc.Room()
        self.source: rtc.AudioSource | None = None
        self.sample_rate: int | None = None

    async def connect(self, first_wav: WavAudio) -> None:
        self.sample_rate = first_wav.sample_rate
        token = make_human_token(self.identity, self.role, self.room_name)
        await self.room.connect(config.LIVEKIT_URL, token)
        self.source = rtc.AudioSource(
            sample_rate=first_wav.sample_rate,
            num_channels=first_wav.channels,
            queue_size_ms=1000,
        )
        track = rtc.LocalAudioTrack.create_audio_track("mic", self.source)
        options = rtc.TrackPublishOptions()
        options.source = rtc.TrackSource.SOURCE_MICROPHONE
        publication = await self.room.local_participant.publish_track(track, options)
        print(
            f"{self.role}: joined {self.room_name!r} as {self.identity!r}; "
            f"published mic ({publication.sid})",
            flush=True,
        )

    async def play_files(self, paths: Sequence[Path], *, pause_s: float) -> None:
        for path in paths:
            wav = read_wav(path)
            await self.play_wav(wav)
            await asyncio.sleep(pause_s)

    async def play_wav(self, wav: WavAudio) -> None:
        if self.source is None or self.sample_rate is None:
            raise RuntimeError(f"{self.role} is not connected")
        if wav.sample_rate != self.sample_rate or wav.channels != NUM_CHANNELS:
            raise ValueError(
                f"{wav.path} is {wav.sample_rate}Hz/{wav.channels}ch, "
                f"expected {self.sample_rate}Hz/{NUM_CHANNELS}ch"
            )

        bytes_per_frame = wav.sample_rate * wav.channels * 2 * FRAME_MS // 1000
        duration = len(wav.pcm) / (wav.sample_rate * wav.channels * 2)
        print(f"{self.role}: playing {wav.path.name} ({duration:.1f}s)", flush=True)
        started = time.monotonic()
        for offset in range(0, len(wav.pcm), bytes_per_frame):
            chunk = wav.pcm[offset : offset + bytes_per_frame]
            if len(chunk) < 2:
                continue
            if len(chunk) % 2:
                chunk = chunk[:-1]
            samples_per_channel = len(chunk) // (2 * wav.channels)
            frame = rtc.AudioFrame(
                chunk,
                wav.sample_rate,
                wav.channels,
                samples_per_channel,
            )
            await self.source.capture_frame(frame)
            # Keep publication close to real-time and avoid flooding the source.
            target_elapsed = (offset + len(chunk)) / (wav.sample_rate * wav.channels * 2)
            sleep_s = target_elapsed - (time.monotonic() - started)
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
        print(f"{self.role}: finished {wav.path.name}", flush=True)

    async def disconnect(self) -> None:
        await self.room.disconnect()
        print(f"{self.role}: disconnected", flush=True)


def read_wav(path: Path) -> WavAudio:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        pcm = wf.readframes(wf.getnframes())
    if sample_width != 2:
        raise ValueError(f"{path} must be 16-bit PCM; got sample width {sample_width}")
    if channels != NUM_CHANNELS:
        raise ValueError(f"{path} must be mono; got {channels} channels")
    return WavAudio(path=path, sample_rate=sample_rate, channels=channels, pcm=pcm)


def fixture(name: str) -> Path:
    path = FIXTURE_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"missing fixture {path}")
    return path


def make_human_token(identity: str, role: str, room_name: str) -> str:
    if not (config.LIVEKIT_API_KEY and config.LIVEKIT_API_SECRET):
        raise RuntimeError("LIVEKIT_API_KEY/LIVEKIT_API_SECRET are required")
    metadata = json.dumps({"role": role})
    return (
        api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(role.title())
        .with_metadata(metadata)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )


def parse_paths(values: Sequence[str]) -> list[Path]:
    return [Path(value) if "/" in value or value.endswith(".wav") and Path(value).exists() else fixture(value) for value in values]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="basha-fixture-call")
    parser.add_argument("--room", default="basha-demo", help="LiveKit room name")
    parser.add_argument("--pause", type=float, default=1.25, help="Pause between fixture utterances")
    parser.add_argument(
        "--post-lock-wait",
        type=float,
        default=8.0,
        help="Wait after lock utterances before relay-test audio",
    )
    parser.add_argument(
        "--keepalive",
        type=float,
        default=15.0,
        help="Seconds to stay connected after playback so browser can inspect tracks",
    )
    parser.add_argument(
        "--driver-lock",
        nargs="+",
        default=["kn_pickup.wav", "kn_eta.wav"],
        help="Driver/Kannada lock fixtures",
    )
    parser.add_argument(
        "--customer-lock",
        nargs="+",
        default=["hi_pickup.wav", "hi_otp.wav"],
        help="Customer/Hindi lock fixtures",
    )
    parser.add_argument(
        "--driver-relay",
        nargs="+",
        default=["kn_long.wav"],
        help="Driver fixtures to play after relay gate opens",
    )
    parser.add_argument(
        "--customer-relay",
        nargs="+",
        default=["hi_pickup.wav"],
        help="Customer fixtures to play after relay gate opens",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    if not config.LIVEKIT_URL:
        raise RuntimeError("LIVEKIT_URL is required")

    driver_lock = parse_paths(args.driver_lock)
    customer_lock = parse_paths(args.customer_lock)
    driver_relay = parse_paths(args.driver_relay)
    customer_relay = parse_paths(args.customer_relay)

    driver = SyntheticParticipant(room_name=args.room, role="driver", identity="driver")
    customer = SyntheticParticipant(room_name=args.room, role="customer", identity="customer")

    await asyncio.gather(
        driver.connect(read_wav(driver_lock[0])),
        customer.connect(read_wav(customer_lock[0])),
    )
    try:
        print("\nPhase 4 lock utterances...", flush=True)
        await asyncio.gather(
            driver.play_files(driver_lock, pause_s=args.pause),
            customer.play_files(customer_lock, pause_s=args.pause),
        )

        print(f"\nWaiting {args.post_lock_wait:.1f}s for relay gate to resolve...", flush=True)
        await asyncio.sleep(args.post_lock_wait)

        print("\nPost-lock relay-test utterances...", flush=True)
        await asyncio.gather(
            driver.play_files(driver_relay, pause_s=args.pause),
            customer.play_files(customer_relay, pause_s=args.pause),
        )

        print(f"\nKeeping participants connected for {args.keepalive:.1f}s...", flush=True)
        await asyncio.sleep(args.keepalive)
    finally:
        await asyncio.gather(driver.disconnect(), customer.disconnect(), return_exceptions=True)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
