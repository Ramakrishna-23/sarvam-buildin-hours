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
from .conversation import ConversationMemory, Turn
from .drift import DriftEngine, State, offer_reply
from .mediator import TurnManager, assess, next_action, summary_text
from .pipeline import SAMPLE_RATE, DirectionPipeline
from .stt_realtime import SttSession
from .translate import translate_segment
from .tts import synth_segment_pcm

log = logging.getLogger(__name__)

AGENT_IDENTITY = "agent"
LOCK_FINALS = int(os.getenv("BB_LOCK_FINALS", "1"))
LOCK_MIN_CONF = float(os.getenv("BB_LOCK_MIN_CONF", "0.5"))
FRAME_BYTES = SAMPLE_RATE * 2 // 10  # 100 ms
MEDIATION_ON = os.getenv("BB_MEDIATION", "1") != "0"

OFFER_TEXT = "I can help sort out the pickup. Should I help?"


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
        # Phase 5/6
        self.memory = ConversationMemory()
        self.drift = DriftEngine(self.memory)
        self.turns = TurnManager()
        self._brain_lock = asyncio.Lock()
        self._offered = False

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
            if MEDIATION_ON and getattr(msg, "event", None) == "transcript.final":
                text = (msg.text or "").strip()
                if text:
                    asyncio.create_task(
                        self._brain(identity, text, getattr(msg, "language", None))
                    )

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

    # ── Phase 5/6: drift engine + mediation ───────────────────────────────

    def _role(self, identity: str) -> str:
        if identity in ("rider", "driver"):
            return identity
        order = list(self.people)
        return "rider" if order and order[0] == identity else "driver"

    def _person_for(self, role: str) -> Participant | None:
        for identity, person in self.people.items():
            if self._role(identity) == role:
                return person
        return None

    def _set_paused(self, paused: bool) -> None:
        for person in self.people.values():
            if person.pipeline is not None:
                person.pipeline.paused = paused

    async def _brain(self, identity: str, text: str, lang: str | None) -> None:
        """One drift assessment per finalized utterance, serialized."""
        async with self._brain_lock:
            role = self._role(identity)

            if self.drift.state == State.OFFER_HELP:
                reply = offer_reply(text)
                if reply == "yes":
                    self.drift.accept_offer()
                elif reply == "no":
                    self.drift.decline_offer()

            self.memory.add(
                Turn(role=role, text=text, lang=lang, t=asyncio.get_event_loop().time())
            )
            try:
                assessment = await assess(self.client, self.memory)
            except Exception:
                log.exception("assess failed")
                return
            changed_slots = self.memory.slots.update(assessment.slots)
            result = self.drift.evaluate(assessment)

            await self.emit(
                {
                    "tag": "agent.state",
                    "state": result.state.value,
                    "score": result.score,
                    "signals": result.signals,
                    "evidence": result.evidence[:3],
                }
            )
            if changed_slots:
                await self.emit(
                    {
                        "tag": "slots.updated",
                        "changed": changed_slots,
                        "slots": self.memory.slots.values,
                        "missing": self.memory.slots.missing_required,
                    }
                )
            try:
                await self._act(role)
            except Exception:
                log.exception("mediation action failed")

    async def _act(self, last_speaker_role: str) -> None:
        state = self.drift.state

        if state == State.SAFETY_ESCALATION:
            self._set_paused(True)
            await self._speak("both", "This sounds like an emergency. Connecting you to support now.")
            return

        if state == State.OFFER_HELP and not self._offered:
            self._offered = True
            await self._speak("both", OFFER_TEXT)
            return

        if state != State.ACTIVE_MEDIATION:
            self._set_paused(False)
            return

        # agent owns the floor while mediating
        self._set_paused(True)
        if not self.turns.should_act(last_speaker_role):
            return

        if self.memory.slots.complete:
            await self._speak("both", summary_text(self.memory))
            self.drift.resolve()
            await self.emit({"tag": "agent.state", "state": State.RESOLVED.value,
                             "score": 0, "signals": [], "evidence": []})
            self.drift.back_to_monitor()
            self._offered = False
            self.turns = TurnManager()
            self._set_paused(False)
            return

        action = await next_action(self.client, self.memory)
        self.turns.record(action)
        await self.emit(
            {
                "tag": "mediation.action",
                "action": action.action,
                "target": action.target,
                "utterance": action.utterance,
                "reason": action.reason,
            }
        )
        await self._speak(action.target, action.utterance)
        self.memory.add(Turn(role="agent", text=action.utterance))

    async def _speak(self, target: str, text_en: str) -> None:
        """Translate an English mediator line into each listener's language and
        queue it on their outbound track."""
        targets = (
            [p for p in self.people.values()]
            if target == "both"
            else [p for p in [self._person_for(target)] if p is not None]
        )
        for person in targets:
            if not person.lang:
                continue  # pre-lock: stay silent
            try:
                localized = await translate_segment(
                    self.client, text_en, "en-IN", person.lang
                )
                pcm = await synth_segment_pcm(
                    self.client, localized, person.lang, sample_rate=SAMPLE_RATE
                )
            except Exception:
                log.exception("agent speech failed for %s", person.identity)
                continue
            await self.emit(
                {
                    "tag": "agent.speech",
                    "target": person.identity,
                    "text_en": text_en,
                    "text": localized,
                }
            )
            await person.out_pcm.put((pcm, {"direction": "agent"}))

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
