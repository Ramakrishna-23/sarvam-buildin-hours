"""Partials spike: stream a fixture WAV in real-time-paced chunks to the
realtime STT WS and log every event with wall-clock + audio-time offsets.

Run: uv run python scripts/spike_partials.py fixtures/kn_pickup.wav [fast|balanced]
"""

import asyncio
import base64
import sys
import time
import wave
from pathlib import Path

from sarvamai import AsyncSarvamAI
from sarvamai.types.realtime_audio_input import RealtimeAudioInput
from sarvamai.types.realtime_end import RealtimeEnd
from sarvamai.types.realtime_flush import RealtimeFlush

from basha_bridge import config

CHUNK_MS = 100


async def sender(ws, frames: bytes, sample_rate: int) -> None:
    bytes_per_chunk = sample_rate * 2 * CHUNK_MS // 1000
    audio_ms = 0
    for i in range(0, len(frames), bytes_per_chunk):
        chunk = frames[i : i + bytes_per_chunk]
        await ws.send_realtime_audio_input(
            RealtimeAudioInput(audio=base64.b64encode(chunk).decode())
        )
        audio_ms += CHUNK_MS
        await asyncio.sleep(CHUNK_MS / 1000)  # real-time pacing
    print(f"--- all audio sent ({audio_ms} ms) ---")
    await ws.send_realtime_flush(RealtimeFlush())
    await asyncio.sleep(2)
    await ws.send_realtime_end(RealtimeEnd())


async def receiver(ws, t0: float) -> None:
    async for msg in ws:
        dt = time.monotonic() - t0
        event = getattr(msg, "event", type(msg).__name__)
        text = getattr(msg, "text", "")
        extra = f" | {text!r}" if text else ""
        print(f"[{dt:6.2f}s] {event}{extra}")
        if event == "session.end":
            break


async def main() -> None:
    wav_path = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures/kn_pickup.wav")
    stream_type = sys.argv[2] if len(sys.argv) > 2 else "fast"
    language = sys.argv[3] if len(sys.argv) > 3 else "auto"

    with wave.open(str(wav_path), "rb") as wf:
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        duration = wf.getnframes() / sample_rate
    print(f"fixture: {wav_path.name} ({duration:.1f}s @ {sample_rate} Hz) | stream_type={stream_type}")

    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)
    async with client.speech_to_text_realtime_streaming.connect(
        language_code=language,
        model="saaras:v3-realtime",
        stream_type=stream_type,
        mode="transcribe",
        encoding="linear16",
        sample_rate=str(sample_rate),
        endpointing="vad",
    ) as ws:
        t0 = time.monotonic()
        recv_task = asyncio.create_task(receiver(ws, t0))
        await sender(ws, frames, sample_rate)
        try:
            await asyncio.wait_for(recv_task, timeout=5)
        except asyncio.TimeoutError:
            recv_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
