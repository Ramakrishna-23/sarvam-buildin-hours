"""One always-on directional pipeline: speaker's STT events → chunker →
Mayura → Bulbul → outbound PCM queue.

Two instances run permanently (hi→kn and kn→hi). There is no turn state:
segments commit while the speaker is still mid-sentence.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from sarvamai import AsyncSarvamAI

from .chunker import IncrementalChunker, Segment
from .translate import translate_segment
from .tts import synth_segment_pcm

log = logging.getLogger(__name__)

Emit = Callable[[dict], Awaitable[None]]

SAMPLE_RATE = 16000
MERGE_BACKLOG_AT = 2  # pending segments beyond which we merge into one call
CATCHUP_PACE = 1.15
TRANSLATE_TIMEOUT_S = 4.0  # observed rare 15 s Mayura stalls; retry beats waiting
TTS_TIMEOUT_S = 8.0


async def _with_retry(factory, timeout: float, name: str):
    for attempt in (1, 2):
        try:
            return await asyncio.wait_for(factory(), timeout)
        except Exception:
            if attempt == 2:
                raise
            log.warning("%s timed out/failed; retrying once", name)


class DirectionPipeline:
    """speaker (src_lang) → listener (tgt_lang)."""

    def __init__(
        self,
        client: AsyncSarvamAI,
        *,
        direction_id: str,  # e.g. "driver→rider"
        speaker_identity: str,
        emit: Emit,
        out_pcm: asyncio.Queue,
    ) -> None:
        self.client = client
        self.direction_id = direction_id
        self.speaker_identity = speaker_identity
        self.emit = emit
        self.out_pcm = out_pcm  # items: (bytes pcm_s16le@16k, dict meta)
        self.src_lang: str | None = None
        self.tgt_lang: str | None = None
        self.active = False
        self.paused = False  # true during ACTIVE_MEDIATION: agent owns the floor
        self.chunker = IncrementalChunker()
        self._seg_q: asyncio.Queue[Segment] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []

    def activate(self, src_lang: str, tgt_lang: str) -> None:
        self.src_lang, self.tgt_lang = src_lang, tgt_lang
        self.active = True

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._poll_loop(), name=f"poll:{self.direction_id}"),
            asyncio.create_task(self._worker(), name=f"work:{self.direction_id}"),
        ]

    def stop(self) -> None:
        for t in self._tasks:
            t.cancel()

    # ── STT event intake (called from the speaker's SttSession) ───────────

    async def on_stt_event(self, msg) -> None:
        event = getattr(msg, "event", None)
        if event == "transcript.partial":
            await self.emit(
                {
                    "tag": "utterance.partial",
                    "speaker": self.speaker_identity,
                    "utt": msg.utterance_idx,
                    "text": msg.text,
                    "lang": getattr(msg, "language", None),
                }
            )
            self._enqueue(self.chunker.on_partial(msg.utterance_idx, msg.text))
        elif event == "transcript.final":
            await self.emit(
                {
                    "tag": "utterance.final",
                    "speaker": self.speaker_identity,
                    "utt": msg.utterance_idx,
                    "text": msg.text,
                    "lang": getattr(msg, "language", None),
                    "conf": getattr(msg, "language_confidence", None),
                }
            )
            self._enqueue(self.chunker.on_final(msg.utterance_idx, msg.text))

    def _enqueue(self, segments: list[Segment]) -> None:
        for seg in segments:
            self._seg_q.put_nowait(seg)

    # ── background loops ──────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(0.1)
            self._enqueue(self.chunker.poll())

    async def _worker(self) -> None:
        while True:
            seg = await self._seg_q.get()
            # merge backlog so lag never compounds
            merged_text, first_pending = seg.text, seg.t_first_pending
            merged = 0
            while self._seg_q.qsize() >= MERGE_BACKLOG_AT:
                extra = self._seg_q.get_nowait()
                merged_text += " " + extra.text
                merged += 1
            if not (self.active and self.src_lang and self.tgt_lang) or self.paused:
                continue  # pre-lock or mediating: captions only, no audio
            try:
                await self._speak(merged_text, first_pending, seg, merged)
            except Exception:
                log.exception("[%s] segment failed: %r", self.direction_id, merged_text)

    async def _speak(
        self, text: str, first_pending: float, seg: Segment, merged: int
    ) -> None:
        t0 = time.monotonic()
        translated = await _with_retry(
            lambda: translate_segment(self.client, text, self.src_lang, self.tgt_lang),
            TRANSLATE_TIMEOUT_S,
            f"translate[{self.direction_id}]",
        )
        t_translate = time.monotonic() - t0
        await self.emit(
            {
                "tag": "segment.translated",
                "direction": self.direction_id,
                "speaker": self.speaker_identity,
                "seg": f"{seg.utt_idx}.{seg.seq}",
                "src": text,
                "tgt": translated,
                "reason": seg.reason,
                "merged": merged,
                "translate_ms": round(t_translate * 1000),
            }
        )
        t1 = time.monotonic()
        pace = CATCHUP_PACE if self._seg_q.qsize() >= MERGE_BACKLOG_AT else 1.0
        pcm = await _with_retry(
            lambda: synth_segment_pcm(
                self.client, translated, self.tgt_lang, sample_rate=SAMPLE_RATE, pace=pace
            ),
            TTS_TIMEOUT_S,
            f"tts[{self.direction_id}]",
        )
        t_tts = time.monotonic() - t1
        ear_ms = round((time.monotonic() - first_pending) * 1000)
        await self.emit(
            {
                "tag": "segment.spoken",
                "direction": self.direction_id,
                "speaker": self.speaker_identity,
                "seg": f"{seg.utt_idx}.{seg.seq}",
                "tts_ms": round(t_tts * 1000),
                "ear_ms": ear_ms,
                "audio_s": round(len(pcm) / 2 / SAMPLE_RATE, 2),
            }
        )
        await self.out_pcm.put((pcm, {"direction": self.direction_id}))
