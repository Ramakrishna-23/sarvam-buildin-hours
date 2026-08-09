"""Simulated human participant — joins the room as a real LiveKit peer,
publishes fixture audio from its mic track, and records the translation track
the agent sends back.

This exercises the exact live path (WebRTC → agent → STT → chunker → translate
→ TTS → track) without needing two people in the room.

    uv run basha-bridge simulate --room demo --id driver --fixture kn_pickup.wav
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import wave
from pathlib import Path

from livekit import api, rtc

from . import config

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_MS = 100
FRAME_BYTES = SAMPLE_RATE * 2 * FRAME_MS // 1000
ROOT = Path(__file__).parent.parent


class SimulatedParticipant:
    def __init__(
        self,
        room_name: str,
        identity: str,
        fixture: Path,
        *,
        start_delay: float = 0.0,
        repeat: int = 1,
        gap_s: float = 4.0,
        record_to: Path | None = None,
    ) -> None:
        self.room_name = room_name
        self.identity = identity
        self.fixture = fixture
        self.start_delay = start_delay
        self.repeat = repeat
        self.gap_s = gap_s
        self.record_to = record_to
        self.room = rtc.Room()
        self.received: list[bytes] = []
        self._recv_task: asyncio.Task | None = None

    async def run(self) -> int:
        with wave.open(str(self.fixture), "rb") as w:
            assert w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1, (
                f"fixture must be {SAMPLE_RATE} Hz mono"
            )
            pcm = w.readframes(w.getnframes())

        token = (
            api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
            .with_identity(self.identity)
            .with_grants(api.VideoGrants(room_join=True, room=self.room_name))
            .to_jwt()
        )

        @self.room.on("track_subscribed")
        def _on_track(track, publication, participant):
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            name = publication.name or ""
            if participant.identity == "agent" and name == f"xlate-to-{self.identity}":
                log.info("[%s] receiving %s", self.identity, name)
                self._recv_task = asyncio.create_task(self._record(track))

        @self.room.on("data_received")
        def _on_data(packet):
            try:
                import json

                evt = json.loads(packet.data.decode())
            except Exception:
                return
            tag = evt.get("tag")
            if tag in ("pair.locked", "agent.state", "mediation.action", "agent.speech"):
                log.info("[%s] bus %s: %s", self.identity, tag,
                         {k: v for k, v in evt.items() if k != "tag"})
            elif tag == "segment.translated" and evt.get("speaker") != self.identity:
                log.info("[%s] hears translation: %r", self.identity, evt.get("tgt"))

        await self.room.connect(config.LIVEKIT_URL, token)
        log.info("[%s] joined room %s", self.identity, self.room_name)

        source = rtc.AudioSource(SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track(f"{self.identity}-mic", source)
        await self.room.local_participant.publish_track(track)
        log.info("[%s] published mic track", self.identity)

        if self.start_delay:
            await self._silence(source, self.start_delay)

        for i in range(self.repeat):
            log.info("[%s] speaking %s (take %d)", self.identity, self.fixture.name, i + 1)
            await self._publish_pcm(source, pcm)
            await self._silence(source, self.gap_s)

        # linger so the agent's reply has time to arrive
        await self._silence(source, 8.0)

        if self.record_to and self.received:
            data = b"".join(self.received)
            with wave.open(str(self.record_to), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(data)
            secs = len(data) / 2 / SAMPLE_RATE
            log.info("[%s] wrote %s (%.1f s received)", self.identity, self.record_to, secs)

        await self.room.disconnect()
        heard = sum(len(c) for c in self.received) / 2 / SAMPLE_RATE
        log.info("[%s] done — %.1f s of translated audio received", self.identity, heard)
        return 0 if heard > 0.5 else 1

    async def _publish_pcm(self, source: rtc.AudioSource, pcm: bytes) -> None:
        for i in range(0, len(pcm), FRAME_BYTES):
            chunk = pcm[i : i + FRAME_BYTES]
            if len(chunk) < FRAME_BYTES:
                chunk = chunk + b"\x00" * (FRAME_BYTES - len(chunk))
            await source.capture_frame(
                rtc.AudioFrame(
                    data=chunk,
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                    samples_per_channel=FRAME_BYTES // 2,
                )
            )

    async def _silence(self, source: rtc.AudioSource, seconds: float) -> None:
        for _ in range(int(seconds * 1000 / FRAME_MS)):
            await source.capture_frame(
                rtc.AudioFrame(
                    data=b"\x00" * FRAME_BYTES,
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                    samples_per_channel=FRAME_BYTES // 2,
                )
            )

    async def _record(self, track: rtc.Track) -> None:
        stream = rtc.AudioStream(
            track, sample_rate=SAMPLE_RATE, num_channels=1, frame_size_ms=FRAME_MS
        )
        async for ev in stream:
            data = bytes(ev.frame.data)
            if any(data):  # ignore pure silence padding
                self.received.append(data)


async def run_simulation(
    room: str, identity: str, fixture: str, delay: float, repeat: int, record: str | None
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    path = Path(fixture)
    if not path.exists():
        path = ROOT / "fixtures" / fixture
    sim = SimulatedParticipant(
        room,
        identity,
        path,
        start_delay=delay,
        repeat=repeat,
        record_to=Path(record) if record else None,
    )
    return await sim.run()


def main() -> None:
    p = argparse.ArgumentParser(prog="basha-bridge simulate")
    p.add_argument("--room", default="demo")
    p.add_argument("--id", default="driver")
    p.add_argument("--fixture", default="kn_pickup.wav")
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--record", default=None)
    a = p.parse_args()
    raise SystemExit(
        asyncio.run(run_simulation(a.room, a.id, a.fixture, a.delay, a.repeat, a.record))
    )


if __name__ == "__main__":
    main()
