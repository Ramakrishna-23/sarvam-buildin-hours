"""Phase 2: one-direction live relay over LiveKit.

Agent joins a LiveKit room, subscribes to one human audio track, runs the
Phase-1 relay loop (STT partials -> segmenter -> translate -> TTS), and
publishes a single agent audio track back into the room.

Default direction is driver Kannada -> customer Hindi:

    uv run python -m basha_bridge.livekit_relay_agent --room basha-demo
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
AGENT_TRACK_NAME = "agent-hi"
SAMPLE_RATE = 16000
NUM_CHANNELS = 1


@dataclass
class RelayConfig:
    room_name: str
    source_identity: str = "driver"
    source_language: str = "kn-IN"
    target_language: str = "hi-IN"
    agent_identity: str = AGENT_IDENTITY
    agent_track_name: str = AGENT_TRACK_NAME


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


async def frame_bytes(track: rtc.Track, sample_rate: int = SAMPLE_RATE):
    """Yield mono pcm_s16le bytes from a LiveKit audio track.

    AudioStream can request 16 kHz mono output, so this is the plan's
    48 kHz WebRTC -> 16 kHz STT resampling step.
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
        sample_rate=SAMPLE_RATE,
        num_channels=NUM_CHANNELS,
        queue_size_ms=2000,
    )
    track = rtc.LocalAudioTrack.create_audio_track(track_name, source)
    options = rtc.TrackPublishOptions()
    options.source = rtc.TrackSource.SOURCE_MICROPHONE
    publication = await room.local_participant.publish_track(track, options)
    print(f"published agent track: {track_name} ({publication.sid})")
    return source


async def play_pcm(source: rtc.AudioSource, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    if not pcm:
        return
    # AudioFrame requires full int16 samples.
    if len(pcm) % 2:
        pcm = pcm[:-1]
    if not pcm:
        return
    frame = rtc.AudioFrame(
        pcm,
        sample_rate,
        NUM_CHANNELS,
        len(pcm) // 2,
    )
    await source.capture_frame(frame)


async def relay_track(track: rtc.Track, audio_source: rtc.AudioSource, cfg: RelayConfig) -> None:
    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)
    segmenter = Segmenter()
    segment_queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
    started = time.monotonic()

    async def consume_segments() -> None:
        while True:
            idx, seg = await segment_queue.get()
            try:
                t_commit = time.monotonic() - started
                translated = await translate_segment(
                    client,
                    seg,
                    cfg.source_language,
                    cfg.target_language,
                )
                print(
                    f"[{t_commit:6.2f}s] seg {idx}: {seg!r} -> {translated!r}",
                    flush=True,
                )
                first = True
                async for chunk in synthesize_segment_stream(
                    client,
                    translated,
                    cfg.target_language,
                    sample_rate=SAMPLE_RATE,
                ):
                    if first:
                        print(
                            f"[{time.monotonic() - started:6.2f}s] seg {idx}: first audio",
                            flush=True,
                        )
                        first = False
                    await play_pcm(audio_source, bytes(chunk), SAMPLE_RATE)
            except Exception as exc:
                print(f"segment {idx} failed: {exc!r}", flush=True)
            finally:
                segment_queue.task_done()

    consumer = asyncio.create_task(consume_segments())
    idx = 0
    try:
        async for event, text in stream_partials(
            frame_bytes(track, SAMPLE_RATE),
            language_code=cfg.source_language,
            sample_rate=SAMPLE_RATE,
            stream_type="fast",
        ):
            segments: list[str] = []
            if event == "partial":
                segments = segmenter.feed(text)
            elif event == "final":
                segments = segmenter.flush(text)
            elif event in ("speech_start", "speech_end"):
                print(f"[{time.monotonic() - started:6.2f}s] {event}", flush=True)

            for seg in segments:
                if not seg.strip():
                    continue
                await segment_queue.put((idx, seg))
                idx += 1
    finally:
        await segment_queue.join()
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer


def is_source_audio(track: rtc.Track, participant: rtc.RemoteParticipant, cfg: RelayConfig) -> bool:
    return (
        participant.identity == cfg.source_identity
        and track.kind == rtc.TrackKind.KIND_AUDIO
    )


async def run_agent(cfg: RelayConfig) -> None:
    if not config.LIVEKIT_URL:
        raise RuntimeError("LIVEKIT_URL is required")
    if not config.SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is required")

    room = rtc.Room()
    relay_task: asyncio.Task | None = None
    audio_source: rtc.AudioSource | None = None
    pending_source_tracks: list[tuple[rtc.Track, rtc.RemoteParticipant]] = []

    def start_relay(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        nonlocal relay_task, audio_source
        if not is_source_audio(track, participant, cfg):
            return
        if relay_task and not relay_task.done():
            print("relay already running; ignoring duplicate source track")
            return
        if audio_source is None:
            print(f"source track from {participant.identity} arrived before agent output was ready; deferring")
            pending_source_tracks.append((track, participant))
            return
        print(f"subscribed to source track from {participant.identity}; starting relay")
        relay_task = asyncio.create_task(relay_track(track, audio_source, cfg))

    @room.on("track_subscribed")
    def on_track_subscribed(
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        print(
            f"track_subscribed participant={participant.identity} "
            f"track={publication.name} kind={track.kind}",
            flush=True,
        )
        start_relay(track, participant)

    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        print(f"participant connected: {participant.identity}", flush=True)

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        print(f"participant disconnected: {participant.identity}", flush=True)

    token = make_token(cfg.agent_identity, cfg.room_name)
    await room.connect(config.LIVEKIT_URL, token)
    print(f"agent joined room {room.name!r} as {cfg.agent_identity!r}")
    audio_source = await publish_agent_track(room, cfg.agent_track_name)

    # Handle tracks that were subscribed during connect before the agent output
    # track was ready, then scan any already-present participants.
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
        if relay_task:
            relay_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await relay_task
        await room.disconnect()


def parse_args() -> RelayConfig:
    parser = argparse.ArgumentParser(prog="basha-livekit-agent")
    parser.add_argument("--room", required=True, help="LiveKit room name")
    parser.add_argument("--source-identity", default="driver")
    parser.add_argument("--source-language", default="kn-IN")
    parser.add_argument("--target-language", default="hi-IN")
    parser.add_argument("--agent-identity", default=AGENT_IDENTITY)
    parser.add_argument("--agent-track-name", default=AGENT_TRACK_NAME)
    args = parser.parse_args()
    return RelayConfig(
        room_name=args.room,
        source_identity=args.source_identity,
        source_language=args.source_language,
        target_language=args.target_language,
        agent_identity=args.agent_identity,
        agent_track_name=args.agent_track_name,
    )


def main() -> None:
    asyncio.run(run_agent(parse_args()))


if __name__ == "__main__":
    main()
