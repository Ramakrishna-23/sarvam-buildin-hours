"""LiveKit agent worker: joins a room, runs one always-on translation pipeline
per human participant, publishes one `xlate-to-<identity>` audio track per
listener, and mirrors all bus events onto the room data channel.

    uv run basha-bridge agent --room demo
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from livekit import api, rtc
from sarvamai import AsyncSarvamAI

from . import config
from .pipeline import SAMPLE_RATE, DirectionPipeline
from .stt_realtime import SttSession

log = logging.getLogger(__name__)

AGENT_IDENTITY = "agent"
LOCK_FINALS = int(os.getenv("BB_LOCK_FINALS", "1"))
LOCK_MIN_CONF = float(os.getenv("BB_LOCK_MIN_CONF", "0.5"))
FRAME_BYTES = SAMPLE_RATE * 2 // 10  # 100 ms


class Participant:
    def __init__(self, identity: str) -> None:
        self.identity = identity
        self.stt: SttSession | None = None
        self.pipeline: DirectionPipeline | None = None  # this person speaking → other
        self.out_pcm: asyncio.Queue = asyncio.Queue()  # audio TO this person
        self.source: rtc.AudioSource | None = None
        self.lang: str | None = None
        self._lang_votes: list[str] = []


class AgentSession:
    def __init__(self, room_name: str) -> None:
        self.room_name = room_name
        self.client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)
        self.room = rtc.Room()
        self.people: dict[str, Participant] = {}
        self.locked = False

    # ── bus ───────────────────────────────────────────────────────────────

    async def emit(self, evt: dict) -> None:
        try:
            await self.room.local_participant.publish_data(
                json.dumps(evt, ensure_ascii=False).encode(), reliable=True, topic="bus"
            )
        except Exception:
            log.exception("publish_data failed")

    # ── lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        token = (
            api.AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
            .with_identity(AGENT_IDENTITY)
            .with_grants(api.VideoGrants(room_join=True, room=self.room_name))
            .to_jwt()
        )

        @self.room.on("track_subscribed")
        def _on_track(track, publication, participant):
            if (
                track.kind == rtc.TrackKind.KIND_AUDIO
                and participant.identity != AGENT_IDENTITY
            ):
                asyncio.create_task(self._on_human_audio(track, participant.identity))

        await self.room.connect(config.LIVEKIT_URL, token)
        log.info("agent joined room %s", self.room_name)
        await asyncio.Event().wait()  # run forever

    async def _on_human_audio(self, track: rtc.Track, identity: str) -> None:
        person = self.people.get(identity)
        if person is None:
            person = self.people[identity] = Participant(identity)
            await self._wire_person(person)
        log.info("subscribed to %s mic", identity)

        stream = rtc.AudioStream(
            track, sample_rate=SAMPLE_RATE, num_channels=1, frame_size_ms=100
        )
        async for ev in stream:
            if person.stt is not None:
                await person.stt.send_audio(bytes(ev.frame.data))

    async def _wire_person(self, person: Participant) -> None:
        # outbound track carrying translations TO this person
        person.source = rtc.AudioSource(SAMPLE_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track(
            f"xlate-to-{person.identity}", person.source
        )
        await self.room.local_participant.publish_track(track)
        asyncio.create_task(self._player(person), name=f"play:{person.identity}")

        async def on_stt_event(msg, identity=person.identity) -> None:
            await self._maybe_lock(identity, msg)
            p = self.people[identity]
            if p.pipeline is not None:
                await p.pipeline.on_stt_event(msg)

        person.stt = SttSession(
            self.client, on_event=on_stt_event, language_code="auto", name=person.identity
        )
        asyncio.create_task(person.stt.run(), name=f"stt:{person.identity}")

        self._maybe_build_pipelines()

    def _maybe_build_pipelines(self) -> None:
        if len(self.people) != 2:
            return
        a, b = self.people.values()
        for speaker, listener in ((a, b), (b, a)):
            if speaker.pipeline is None:
                speaker.pipeline = DirectionPipeline(
                    self.client,
                    direction_id=f"{speaker.identity}→{listener.identity}",
                    speaker_identity=speaker.identity,
                    emit=self.emit,
                    out_pcm=listener.out_pcm,
                )
                speaker.pipeline.start()

    # ── language pair lock ────────────────────────────────────────────────

    async def _maybe_lock(self, identity: str, msg) -> None:
        if self.locked or getattr(msg, "event", None) != "transcript.final":
            return
        lang = getattr(msg, "language", None)
        conf = getattr(msg, "language_confidence", None)
        if not lang or (conf is not None and conf < LOCK_MIN_CONF):
            return
        person = self.people[identity]
        person._lang_votes.append(lang)
        if person._lang_votes.count(lang) >= LOCK_FINALS:
            person.lang = lang
        locked_people = [p for p in self.people.values() if p.lang]
        if len(locked_people) != 2:
            return
        a, b = locked_people
        if a.lang == b.lang:
            await self.emit({"tag": "pair.same", "lang": a.lang})
            return  # stay passive; keep watching (re-lock not needed for demo)
        self.locked = True
        for speaker, listener in ((a, b), (b, a)):
            speaker.pipeline.activate(speaker.lang, listener.lang)
            if speaker.stt is not None:
                await speaker.stt.set_language(speaker.lang)  # pin, re-arm never
        await self.emit(
            {"tag": "pair.locked", "pair": {a.identity: a.lang, b.identity: b.lang}}
        )
        log.info("pair locked: %s=%s %s=%s", a.identity, a.lang, b.identity, b.lang)

    # ── playback ──────────────────────────────────────────────────────────

    async def _player(self, person: Participant) -> None:
        """Drains translated PCM into this person's xlate track, 100 ms frames."""
        while True:
            pcm, _meta = await person.out_pcm.get()
            await self.emit(
                {"tag": "tts.active", "target": person.identity, "active": True}
            )
            for i in range(0, len(pcm), FRAME_BYTES):
                chunk = pcm[i : i + FRAME_BYTES]
                frame = rtc.AudioFrame(
                    data=chunk,
                    sample_rate=SAMPLE_RATE,
                    num_channels=1,
                    samples_per_channel=len(chunk) // 2,
                )
                await person.source.capture_frame(frame)
            if person.out_pcm.empty():
                await self.emit(
                    {"tag": "tts.active", "target": person.identity, "active": False}
                )


async def run_agent(room_name: str) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    await AgentSession(room_name).start()
