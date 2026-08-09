"""Phase 4: cold-start language lock + relay gate over LiveKit.

Agent joins one LiveKit room, subscribes only to human microphone tracks, and
starts silent. It buffers early utterances per side, detects language with
Sarvam REST STT using ``language_code="unknown"`` plus text LID confirmation,
then locks the pair after two consistent detections per side.

Baseline rule from the plan:

* same language -> agent stays silent for the whole call
* mismatch -> simultaneous relay starts in both directions

There is intentionally no turn gate, drift classifier, or mediation layer here.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import math
import time
import wave
from collections import Counter
from dataclasses import dataclass, field

from livekit import api, rtc
from sarvamai import AsyncSarvamAI

from . import config
from .segmenter import Segmenter
from .stt_rest import detect_language_with_text_lid
from .stt_stream import stream_partials
from .translate import translate_segment
from .tts import synthesize_segment_stream

AGENT_IDENTITY = "relay-agent"
AGENT_TO_CUSTOMER_TRACK_NAME = "agent-to-customer"
AGENT_TO_DRIVER_TRACK_NAME = "agent-to-driver"
LEGACY_AGENT_HI_TRACK_NAME = "agent-hi"
LEGACY_AGENT_KN_TRACK_NAME = "agent-kn"
STT_SAMPLE_RATE = 16000
TTS_SAMPLE_RATE = 16000
LIVEKIT_SAMPLE_RATE = 48000
NUM_CHANNELS = 1
FRAME_MS = 20
HUMAN_ROLES = {"driver", "customer"}
ROLE_TRACK_NAMES = {
    "customer": AGENT_TO_CUSTOMER_TRACK_NAME,
    "driver": AGENT_TO_DRIVER_TRACK_NAME,
}
SUPPORTED_RELAY_LANGUAGES = {"hi-IN", "kn-IN"}
LOCK_DETECTIONS_PER_ROLE = 2
VAD_RMS_THRESHOLD = 550
MIN_UTTERANCE_MS = 500
END_SILENCE_MS = 700
MAX_UTTERANCE_MS = 6000
GATE_EVENT_TOPIC = "relay_gate"


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


@dataclass(frozen=True)
class RelayConfig:
    room_name: str
    agent_identity: str = AGENT_IDENTITY


@dataclass
class RoleLanguageState:
    counts: Counter[str] = field(default_factory=Counter)
    samples: list[dict[str, object]] = field(default_factory=list)
    locked_language: str | None = None


@dataclass(frozen=True)
class GateSnapshot:
    mode: str
    languages: dict[str, str]
    directions: tuple[DirectionConfig, ...]


class RelayGate:
    """Phase 4 pair lock and same-language relay gate."""

    def __init__(self) -> None:
        self.states = {role: RoleLanguageState() for role in HUMAN_ROLES}
        self.event = asyncio.Event()
        self.mode = "detecting"
        self.languages: dict[str, str] = {}
        self.directions: tuple[DirectionConfig, ...] = ()
        self._lock = asyncio.Lock()

    def is_resolved(self) -> bool:
        return self.event.is_set()

    def direction_for_role(self, role: str) -> DirectionConfig | None:
        for direction in self.directions:
            if direction.source_role == role:
                return direction
        return None

    def snapshot(self) -> GateSnapshot:
        return GateSnapshot(
            mode=self.mode,
            languages=dict(self.languages),
            directions=self.directions,
        )

    async def record_detection(
        self,
        role: str,
        *,
        language: str,
        transcript: str,
        stt_language: str,
        text_lid_language: str,
        stt_probability: float | None,
    ) -> GateSnapshot | None:
        """Record one utterance language and return a snapshot if gate resolved."""
        if role not in self.states or language not in SUPPORTED_RELAY_LANGUAGES:
            return None

        async with self._lock:
            if self.event.is_set():
                return None

            state = self.states[role]
            state.counts[language] += 1
            state.samples.append(
                {
                    "language": language,
                    "stt_language": stt_language,
                    "text_lid_language": text_lid_language,
                    "stt_probability": stt_probability,
                    "transcript": transcript,
                }
            )
            print(
                f"language sample role={role} language={language} "
                f"count={state.counts[language]}/{LOCK_DETECTIONS_PER_ROLE} "
                f"stt={stt_language} lid={text_lid_language or '-'} "
                f"p={stt_probability if stt_probability is not None else '-'} "
                f"text={transcript!r}",
                flush=True,
            )

            if state.locked_language is None and state.counts[language] >= LOCK_DETECTIONS_PER_ROLE:
                state.locked_language = language
                print(f"language locked role={role} language={language}", flush=True)

            if not all(s.locked_language for s in self.states.values()):
                return None

            self.languages = {
                role_name: role_state.locked_language or "unknown"
                for role_name, role_state in self.states.items()
            }
            if len(set(self.languages.values())) == 1:
                self.mode = "silent"
                self.directions = ()
                print(f"relay gate resolved: same language {self.languages}; staying silent", flush=True)
            else:
                self.mode = "relay"
                self.directions = make_directions(self.languages)
                print(
                    "relay gate resolved: mismatch "
                    f"{self.languages}; starting {[d.label for d in self.directions]}",
                    flush=True,
                )
            self.event.set()
            return self.snapshot()


def make_directions(languages: dict[str, str]) -> tuple[DirectionConfig, ...]:
    driver_lang = languages["driver"]
    customer_lang = languages["customer"]
    return (
        DirectionConfig(
            source_identity="driver",
            source_role="driver",
            source_language=driver_lang,
            target_role="customer",
            target_language=customer_lang,
            agent_track_name=ROLE_TRACK_NAMES["customer"],
        ),
        DirectionConfig(
            source_identity="customer",
            source_role="customer",
            source_language=customer_lang,
            target_role="driver",
            target_language=driver_lang,
            agent_track_name=ROLE_TRACK_NAMES["driver"],
        ),
    )


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
    """Yield mono pcm_s16le bytes from a LiveKit audio track."""
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
    """Publish finite TTS PCM as 48 kHz framed LiveKit audio."""

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


def is_human_audio(
    track: rtc.Track,
    participant: rtc.RemoteParticipant,
    cfg: RelayConfig,
) -> tuple[bool, str | None]:
    """Return whether a track is a human source and the associated role.

    This is the structural self-echo guard: the agent ignores every non-human
    participant and especially its own `relay-agent` publications.
    """
    if track.kind != rtc.TrackKind.KIND_AUDIO:
        return False, None
    if participant.identity == cfg.agent_identity:
        return False, None
    role = participant_role(participant)
    if role not in HUMAN_ROLES:
        return False, None
    return True, role


def pcm_rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples = memoryview(pcm).cast("h")
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def pcm_to_wav_bytes(pcm: bytes, sample_rate: int = STT_SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(NUM_CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return out.getvalue()


async def utterance_pcm(track: rtc.Track, stop_event: asyncio.Event):
    """Yield speech utterances from a track using a simple energy VAD."""
    stream = rtc.AudioStream.from_track(
        track=track,
        sample_rate=STT_SAMPLE_RATE,
        num_channels=NUM_CHANNELS,
        frame_size_ms=100,
    )
    active = False
    speech = bytearray()
    voiced_ms = 0
    silence_ms = 0
    try:
        async for event in stream:
            if stop_event.is_set():
                break
            pcm = bytes(event.frame.data.cast("b"))
            frame_ms = int(1000 * event.frame.samples_per_channel / event.frame.sample_rate)
            rms = pcm_rms(pcm)
            voiced = rms >= VAD_RMS_THRESHOLD

            if voiced:
                if not active:
                    active = True
                    speech.clear()
                    voiced_ms = 0
                    silence_ms = 0
                speech.extend(pcm)
                voiced_ms += frame_ms
                silence_ms = 0
            elif active:
                speech.extend(pcm)
                silence_ms += frame_ms

            if active and (
                silence_ms >= END_SILENCE_MS
                or voiced_ms + silence_ms >= MAX_UTTERANCE_MS
            ):
                if voiced_ms >= MIN_UTTERANCE_MS:
                    yield bytes(speech)
                active = False
                speech.clear()
                voiced_ms = 0
                silence_ms = 0
    finally:
        await stream.aclose()


async def detect_track_languages(
    track: rtc.Track,
    role: str,
    gate: RelayGate,
    room: rtc.Room,
) -> None:
    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)
    async for pcm in utterance_pcm(track, gate.event):
        if gate.is_resolved():
            break
        wav_bytes = pcm_to_wav_bytes(pcm)
        audio_file = io.BytesIO(wav_bytes)
        audio_file.name = f"{role}-utterance.wav"
        try:
            detection = await detect_language_with_text_lid(client, audio_file)
        except Exception as exc:
            print(f"language detection failed role={role}: {exc!r}", flush=True)
            continue

        snapshot = await gate.record_detection(
            role,
            language=detection.language_code,
            transcript=detection.transcript,
            stt_language=detection.stt_language,
            text_lid_language=detection.text_lid_language,
            stt_probability=detection.stt_probability,
        )
        if snapshot:
            publish_gate_state(room, snapshot)
            break


def publish_gate_state(
    room: rtc.Room,
    snapshot: GateSnapshot,
    destination_identities: list[str] | None = None,
) -> None:
    payload = {
        "type": "relay_gate",
        "mode": snapshot.mode,
        "languages": snapshot.languages,
        "tracks": ROLE_TRACK_NAMES,
    }
    room.local_participant.publish_data(
        json.dumps(payload),
        reliable=True,
        destination_identities=destination_identities or [],
        topic=GATE_EVENT_TOPIC,
    )
    print(f"published relay gate event: {payload}", flush=True)


async def handle_human_track(
    track: rtc.Track,
    participant: rtc.RemoteParticipant,
    role: str,
    gate: RelayGate,
    audio_sources: dict[str, rtc.AudioSource],
    room: rtc.Room,
) -> None:
    if not gate.is_resolved():
        print(f"detecting language for {role} from {participant.identity}", flush=True)
        await detect_track_languages(track, role, gate, room)

    if not gate.is_resolved():
        await gate.event.wait()

    if gate.mode == "silent":
        print(f"relay disabled for {role}: same-language pair {gate.languages}", flush=True)
        return

    direction = gate.direction_for_role(role)
    if direction is None:
        print(f"no relay direction for role={role}; gate={gate.snapshot()}", flush=True)
        return

    audio_source = audio_sources[direction.agent_track_name]
    print(
        f"subscribed to source track from {participant.identity}; "
        f"starting relay {direction.label} -> {direction.agent_track_name}",
        flush=True,
    )
    await relay_track(track, audio_source, direction)


def consume_task_result(task: asyncio.Task, task_key: str) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        exc = task.exception()
        if exc:
            print(f"track task {task_key} failed: {exc!r}", flush=True)


async def run_agent(cfg: RelayConfig) -> None:
    if not config.LIVEKIT_URL:
        raise RuntimeError("LIVEKIT_URL is required")
    if not config.SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is required")

    room = rtc.Room()
    gate = RelayGate()
    track_tasks: dict[str, asyncio.Task[None]] = {}
    audio_sources: dict[str, rtc.AudioSource] = {}
    pending_source_tracks: list[tuple[rtc.Track, rtc.RemoteParticipant, str]] = []

    def start_human_track(track: rtc.Track, participant: rtc.RemoteParticipant) -> None:
        is_source, role = is_human_audio(track, participant, cfg)
        if not is_source or role is None:
            return

        existing = track_tasks.get(role)
        if existing and not existing.done():
            print(f"track task already running for {role}; ignoring duplicate source track", flush=True)
            return

        if not all(track_name in audio_sources for track_name in ROLE_TRACK_NAMES.values()):
            print(
                f"source track from {participant.identity} role={role} arrived "
                "before agent output was ready; deferring",
                flush=True,
            )
            pending_source_tracks.append((track, participant, role))
            return

        task = asyncio.create_task(
            handle_human_track(track, participant, role, gate, audio_sources, room),
            name=f"human-track-{role}",
        )
        track_tasks[role] = task
        task.add_done_callback(lambda done, key=role: consume_task_result(done, key))

    def cancel_track_for(participant: rtc.RemoteParticipant) -> None:
        role = participant_role(participant)
        if role in HUMAN_ROLES:
            task = track_tasks.get(role)
            if task and not task.done():
                print(f"cancelling track task for disconnected {role}", flush=True)
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
        start_human_track(track, participant)

    @room.on("participant_connected")
    def on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        print(
            f"participant connected: {participant.identity} role={participant_role(participant)}",
            flush=True,
        )
        if gate.is_resolved():
            publish_gate_state(room, gate.snapshot(), [participant.identity])

    @room.on("participant_disconnected")
    def on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        print(f"participant disconnected: {participant.identity}", flush=True)
        cancel_track_for(participant)

    token = make_token(cfg.agent_identity, cfg.room_name)
    await room.connect(config.LIVEKIT_URL, token)
    print(f"agent joined room {room.name!r} as {cfg.agent_identity!r}", flush=True)

    for track_name in ROLE_TRACK_NAMES.values():
        audio_sources[track_name] = await publish_agent_track(room, track_name)

    # Handle tracks that were subscribed during connect before the agent output
    # tracks were ready, then scan any already-present participants.
    for track, participant, _role in pending_source_tracks:
        start_human_track(track, participant)
    pending_source_tracks.clear()
    for participant in room.remote_participants.values():
        for publication in participant.track_publications.values():
            if publication.track:
                start_human_track(publication.track, participant)

    try:
        while room.isconnected():
            await asyncio.sleep(1)
    finally:
        for task in track_tasks.values():
            task.cancel()
        if track_tasks:
            await asyncio.gather(*track_tasks.values(), return_exceptions=True)
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
