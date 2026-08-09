import asyncio
import base64
from typing import AsyncIterator

from sarvamai import AsyncSarvamAI
from sarvamai.types.realtime_audio_input import RealtimeAudioInput
from sarvamai.types.realtime_end import RealtimeEnd
from sarvamai.types.realtime_flush import RealtimeFlush

from . import config


async def stream_partials(
    chunks: AsyncIterator[bytes],
    language_code: str,
    sample_rate: int = 16000,
    stream_type: str = "fast",
) -> AsyncIterator[tuple[str, str]]:
    """Feed raw pcm_s16le chunks, yield (event, text) where event is
    partial | final | speech_start | speech_end.

    language_code must be explicit — 'auto' on the realtime model
    mis-transcribes (verified in partials spike)."""
    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)
    async with client.speech_to_text_realtime_streaming.connect(
        language_code=language_code,
        model="saaras:v3-realtime",
        stream_type=stream_type,
        mode="transcribe",
        encoding="linear16",
        sample_rate=str(sample_rate),
        endpointing="vad",
    ) as ws:

        async def send() -> None:
            async for chunk in chunks:
                await ws.send_realtime_audio_input(
                    RealtimeAudioInput(audio=base64.b64encode(chunk).decode())
                )
            await ws.send_realtime_flush(RealtimeFlush())
            await ws.send_realtime_end(RealtimeEnd())

        send_task = asyncio.create_task(send())
        try:
            async for msg in ws:
                event = getattr(msg, "event", "")
                if event in ("transcript.partial", "transcript.final"):
                    yield event.removeprefix("transcript."), msg.text or ""
                elif event in ("vad.speech_start", "vad.speech_end"):
                    yield event.removeprefix("vad."), ""
                elif event == "session.end":
                    break
        finally:
            send_task.cancel()
