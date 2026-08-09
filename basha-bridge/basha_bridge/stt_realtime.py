"""saaras:v3-realtime streaming STT session.

Feeds 16 kHz pcm_s16le frames in, surfaces transcript.partial /
transcript.final / vad.* events to a callback. Supports mid-stream language
pinning via config.update (no socket restart).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Awaitable, Callable

from sarvamai import AsyncSarvamAI
from sarvamai.types import RealtimeAudioInput, RealtimeConfigUpdate, RealtimeFlush

log = logging.getLogger(__name__)

EventHandler = Callable[[object], Awaitable[None]]


class SttSession:
    def __init__(
        self,
        client: AsyncSarvamAI,
        *,
        on_event: EventHandler,
        language_code: str = "auto",
        silence_duration_ms: int = 400,
        name: str = "stt",
    ) -> None:
        self._client = client
        self._on_event = on_event
        self._language_code = language_code
        self._silence_ms = silence_duration_ms
        self._name = name
        self._audio_q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
        self._sock = None
        self._closed = False

    async def send_audio(self, pcm: bytes) -> None:
        await self._audio_q.put(pcm)

    async def flush(self) -> None:
        if self._sock is not None:
            await self._sock.send_realtime_flush(RealtimeFlush())

    async def set_language(self, language_code: str) -> None:
        self._language_code = language_code
        if self._sock is not None:
            await self._sock.send_realtime_config_update(
                RealtimeConfigUpdate(language_code=language_code)
            )

    def close(self) -> None:
        self._closed = True
        self._audio_q.put_nowait(None)

    async def run(self) -> None:
        """Connect + pump until close(); reconnects on transient errors."""
        while not self._closed:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._closed:
                    return
                log.exception("[%s] socket dropped; reconnecting in 1s", self._name)
                await asyncio.sleep(1)

    async def _run_once(self) -> None:
        async with self._client.speech_to_text_realtime_streaming.connect(
            model="saaras:v3-realtime",
            language_code=self._language_code,
            stream_type="fast",
            mode="transcribe",
            endpointing="vad",
            encoding="linear16",
            sample_rate="16000",
            silence_duration_ms=str(self._silence_ms),
        ) as sock:
            self._sock = sock
            sender = asyncio.create_task(self._sender(sock))
            try:
                async for msg in sock:
                    await self._on_event(msg)
                    if self._closed:
                        return
            finally:
                self._sock = None
                sender.cancel()

    async def _sender(self, sock) -> None:
        while True:
            pcm = await self._audio_q.get()
            if pcm is None:
                return
            await sock.send_realtime_audio_input(
                RealtimeAudioInput(audio=base64.b64encode(pcm).decode())
            )
