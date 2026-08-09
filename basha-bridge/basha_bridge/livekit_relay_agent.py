"""Phase 3: full-duplex live relay over LiveKit.

Agent joins one LiveKit room, subscribes only to human microphone tracks, and
runs two simultaneous Sarvam relay pipelines:

    driver   kn-IN -> customer hi-IN  on agent-hi
    customer hi-IN -> driver   kn-IN  on agent-kn

There is intentionally no turn gate or drift/mediation layer here. Phase 3 is
baseline full-duplex simultaneous relay: if both humans talk at once, both
pipelines keep running independently.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import time
from dataclasses import dataclass

from livekit import api, rtc
from sarvamai import AsyncSarvamAI

from . import config
from .segmenter import Segmenter
from .stt_stream import stream_partials
from .translate import translate_segment
from .tts import synthesize_segment_stream

AGENT_IDENTITY = "relay-agent"
AGENT_HI_TRACK_NAME = "agent-hi"
AGENT_KN_TRACK_NAME = "agent-kn"
STT_SAMPLE_RATE = 16000
TTS_SAMPLE_RATE = 16000
LIVEKIT_SAMPLE_RATE = 48000
NUM_CHANNELS = 1
FRAME_MS = 20
HUMAN_ROLES = {"driver", "customer"}


@dataclass(frozen=True)
class DirectionConfig:
    source_identity: str
    source_role: str
    source_language: str
    target_role: str
    target_language: str
    agent_track_name: str

    @property
    def label(self) -> str:
        return f"{self.source_role} {self.source_language}->{self.target_role} {self.target_language}"


DEFAULT_DIRECTIONS: tuple[DirectionConfig, ...] = (
    DirectionConfig(
        source_identity="driver",
        source_role="driver",
        source_language="kn-IN",
        target_role="customer",
        target_language="hi-IN",
        agent_track_name=AGENT_HI_TRACK_NAME,
    ),
    DirectionConfig(
        source_identity="customer",
        source_role="customer",
        source_language="hi-IN",
        target_role="driver",
        target_language="kn-IN",
        agent_track_name=AGENT_KN_TRACK_NAME,
    ),
)


@dataclass(frozen=True)
class RelayConfig:
    room_name: str
    agent_identity: str = AGENT_IDENTITY
    directions: tuple[DirectionConfig, ...] = DEFAULT_DIRECTIONS


def make_token(identity: str, room_name: str) -> str:
    if not (config.LIVEKIT_API_KEY and config.LIVEKIT_API_SECRET):
        raise RuntimeError("LIVEKIT_API_KEY/LIVEKIT_API_SECRET are required")
    metadata = json.dumps({"role": "agent"})
    return (
        api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name("Basha Bridge Relay Agent")
        .with_metadata(metadata)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
                can_update_own_metadata=True,
                agent=True,
            )
        )
        .to_jwt()
    )


async def frame_bytes(track: rtc.Track, sample_rate: int = STT_SAMPLE_RATE):
    """Yield mono pcm_s16le bytes from a LiveKit audio track.

    AudioStream requests 16 kHz mono output, which is the WebRTC -> STT
    resampling step required by the relay pipeline.
    """
    stream = rtc.AudioStream.from_track(
        track=track,
        sample_rate=sample_rate,
        num_channels=NUM_CHANNELS,
        frame_size_ms=100,
    )
    try:
        async for event in stream:
            yield bytes(event.frame.data.cast("b"))
    finally:
        await stream.aclose()


async def publish_agent_track(room: rtc.Room, track_name: str) -> rtc.AudioSource:
    source = rtc.AudioSource(
        sample_rate=LIVEKIT_SAMPLE_RATE,
        num_channels=NUM_CHANNELS,
        queue_size_ms=1000,
    )
    track = rtc.LocalAudioTrack.create_audio_track(track_name, source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE
    publication = await room.local_participant.publish_track(track, options)
    print(f"published agent track: {track_name} ({publication.sid})", flush=True)
    return source


class LiveKitPcmPublisher:
    """Publish finite TTS PCM as properly framed LiveKit audio.

    Sarvam TTS gives linear16 chunks at TTS_SAMPLE_RATE. LiveKit playback is
    published as 48 kHz mono PCM in small frames. Each relay direction gets its
    own instance, so its output queue cannot block or mingle with the other
    direction.
    """

    def __init__(self, source: rtc.AudioSource):
        self.source = source
        self.resampler = rtc.AudioResampler(
            input_rate=TTS_SAMPLE_RATE,
            output_rate=LIVEKIT_SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self.pending = bytearray()
        self.frame_bytes = LIVEKIT_SAMPLE_RATE * NUM_CHANNELS * 2 * FRAME_MS // 1000

    async def push(self, pcm: bytes) -> None:
        if not pcm:
            return
        if len(pcm) % 2:
            pcm = pcm[:-1]
        for frame in self.resampler.push(bytearray(pcm)):
            await self._push_output(bytes(frame.data.cast("b")))

    async def flush(self) -> None:
        for frame in self.resampler.flush():
            await self._push_output(bytes(frame.data.cast("b")))
        if self.pending:
            await self._capture(bytes(self.pending))
            self.pending.clear()

    async def _push_output(self, data: bytes) -> None:
        self.pending.extend(data)
        while len(self.pending) >= self.frame_bytes:
            frame = bytes(self.pending[: self.frame_bytes])
            del self.pending[: self.frame_bytes]
            await self._capture(frame)

    async def _capture(self, pcm: bytes) -> None:
        if len(pcm) % 2:
            pcm = pcm[:-1]
        if not pcm:
            return
        frame = rtc.AudioFrame(
            pcm,
            LIVEKIT_SAMPLE_RATE,
            NUM_CHANNELS,
            len(pcm) // 2,
        )
        await self.source.capture_frame(frame)


async def relay_track(
    track: rtc.Track,
    audio_source: rtc.AudioSource,
    direction: DirectionConfig,
) -> None:
    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)
    segmenter = Segmenter()
    publisher = LiveKitPcmPublisher(audio_source)
    segment_queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
    started = time.monotonic()
    cancelled = False

    async def consume_segments() -> None:
        while True:
            idx, seg = await segment_queue.get()
            try:
                t_commit = time.monotonic() - started
                translated = await translate_segment(
                    client,
                    seg,
                    direction.source_language,
                    direction.target_language,
                )
                print(
                    f"[{direction.label}] [{t_commit:6.2f}s] seg {idx}: {seg!r} -> {translated!r}",
                    flush=True,
                )
                first = True
                async for chunk in synthesize_segment_stream(
                    client,
                    translated,
                    direction.target_language,
                    sample_rate=TTS_SAMPLE_RATE,
                ):
                    if first:
                        print(
                            f"[{direction.label}] "
                            f"[{time.monotonic() - started:6.2f}s] seg {idx}: first audio",
                            flush=True,
                        )
                        first = False
                    await publisher.push(bytes(chunk))
                await publisher.flush()
            except Exception as exc:
                print(f"[{direction.label}] segment {idx} failed: {exc!r}", flush=True)
            finally:
                segment_queue.task_done()

    consumer = asyncio.create_task(
        consume_segments(),
        name=f"segments-{direction.source_role}-to-{direction.target_role}",
    )
    idx = 0
    try:
        async for event, text in stream_partials(
            frame_bytes(track, STT_SAMPLE_RATE),
            language_code=direction.source_language,
            sample_rate=STT_SAMPLE_RATE,
            stream_type="fast",
        ):
            segments: list[str] = []
            if event == "partial":
                segments = segmenter.feed(text)
            elif event == "final":
                segments = segmenter.flush(text)
            elif event in ("speech_start", "speech_end"):
                print(
                    f"[{direction.label}] [{time.monotonic() - started:6.2f}s] {event}",
                    flush=True,
                )

            for seg in segments:
                if not seg.strip():
                    continue
                await segment_queue.put((idx, seg))
                idx += 1
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        if not cancelled:
            await segment_queue.join()
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer


def participant_role(participant: rtc.RemoteParticipant) -> str | None:
    try:
        metadata = json.loads(participant.metadata or "{}")
        role = metadata.get("role")
        if isinstance(role, str):
            return role
    except json.JSONDecodeError:
        pass
    if participant.identity == AGENT_IDENTITY:
        return "agent"
    if participant.identity in HUMAN_ROLES:
        return participant.identity
    return None


def direction_for_audio(
    track: rtc.Track,
    participant: rtc.RemoteParticipant,
    cfg: RelayConfig,
) -> DirectionConfig | None:
    """Return the relay direction for a human audio track, otherwise None.

    This is the Phase 3 self-echo guard: the agent structurally ignores every
    non-human participant and especially its own `relay-agent` publications.
    """
    if track.kind != rtc.TrackKind.KIND_AUDIO:
        return None
    if participant.identity == cfg.agent_identity:
        return None

    role = participant_role(participant)
    if role not in HUMAN_ROLES:
        return None

    for direction in cfg.directions:
        if participant.identity == direction.source_identity or role == direction.source_role:
            return direction
    return None


def consume_task_result(task: asyncio.Task, task_key: str) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        exc = task.exception()
        if exc:
            print(f"relay task {task_key} failed: {exc!r}", flush=True)


async def run_agent(cfg: RelayConfig) -> None:
    if not config.LIVEKIT_URL:
        raise RuntimeError("LIVEKIT_URL is required")
    if not config.SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is required")

    room = rtc.Room()
    relay_tasks: dict[str, asyncio.Task[None]] = {}
    audio_sources: dict[str, rtc.AudioSource] = {}
    pending_source_tracks: list[tuple[rtc.Track, rtc.RemoteParticipant]] = []

    def start_relay(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        direction = direction_for_audio(track, participant, cfg)
        if direction is None:
            return

        task_key = direction.source_role
        existing = relay_tasks.get(task_key)
        if existing and not existing.done():
            print(f"relay already running for {direction.label}; ignoring duplicate source track", flush=True)
            return

        audio_source = audio_sources.get(direction.agent_track_name)
        if audio_source is None:
            print(
                f"source track from {participant.identity} for {direction.label} arrived "
                "before agent output was ready; deferring",
                flush=True,
            )
            pending_source_tracks.append((track, participant))
            return

        print(
            f"subscribed to source track from {participant.identity}; "
            f"starting relay {direction.label} -> {direction.agent_track_name}",
            flush=True,
        )
        task = asyncio.create_task(
            relay_track(track, audio_source, direction),
            name=f"relay-{direction.source_role}-to-{direction.target_role}",
        )
        relay_tasks[task_key] = task
        task.add_done_callback(lambda done, key=task_key: consume_task_result(done, key))

    def cancel_relay_for(participant: rtc.RemoteParticipant) -> None:
        role = participant_role(participant)
        for direction in cfg.directions:
            if participant.identity == direction.source_identity or role == direction.source_role:
                task = relay_tasks.get(direction.source_role)
                if task and not task.done():
                    print(f"cancelling relay for disconnected {direction.source_role}", flush=True)
                    task.cancel()

    @room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        print(
            f"track_subscribed participant={participant.identity} "
            f"role={participant_role(participant)} "
            f"track={publication.name} kind={track.kind}",
            flush=True,
        )
        start_relay(track, participant)

    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        print(
            f"participant connected: {participant.identity} role={participant_role(participant)}",
            flush=True,
        )

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        print(f"participant disconnected: {participant.identity}", flush=True)
        cancel_relay_for(participant)

    token = make_token(cfg.agent_identity, cfg.room_name)
    await room.connect(config.LIVEKIT_URL, token)
    print(f"agent joined room {room.name!r} as {cfg.agent_identity!r}", flush=True)

    for direction in cfg.directions:
        audio_sources[direction.agent_track_name] = await publish_agent_track(
            room,
            direction.agent_track_name,
        )

    # Handle tracks that were subscribed during connect before the agent output
    # tracks were ready, then scan any already-present participants.
    for track, participant in pending_source_tracks:
        start_relay(track, participant)
    pending_source_tracks.clear()
    for participant in room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.track:
                start_relay(publication.track, participant)

    try:
        while room.isconnected():
            await asyncio.sleep(1)
    finally:
        for task in relay_tasks.values():
            task.cancel()
        if relay_tasks:
            await asyncio.gather(*relay_tasks.values(), return_exceptions=True)
        await room.disconnect()


def parse_args() -> RelayConfig:
    parser = argparse.ArgumentParser(prog="basha-livekit-agent")
    parser.add_argument("--room", required=True, help="LiveKit room name")
    parser.add_argument("--agent-identity", default=AGENT_IDENTITY)
    args = parser.parse_args()
    return RelayConfig(
        room_name=args.room,
        agent_identity=args.agent_identity,
    )


def main() -> None:
    asyncio.run(run_agent(parse_args()))


if __name__ == "__main__":
    main()
