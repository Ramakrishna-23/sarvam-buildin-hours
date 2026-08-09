"""Phase 1 artifact: simulate a live relay against a fixture WAV.

Streams the WAV in real-time-paced 100 ms chunks -> realtime STT partials ->
segment commits -> Mayura translate -> Bulbul TTS, all while "audio" is still
playing. Writes out.wav and prints a per-segment latency table.

Run: uv run python -m basha_bridge.offline_pipeline fixtures/kn_pickup.wav
"""

import asyncio
import io
import sys
import time
import wave
from pathlib import Path

from sarvamai import AsyncSarvamAI

from . import config
from .segmenter import Segmenter
from .stt_rest import detect_language
from .stt_stream import stream_partials
from .translate import translate_segment
from .tts import synthesize_segment

CHUNK_MS = 100
PAIR = {"kn-IN": "hi-IN", "hi-IN": "kn-IN"}


async def paced_chunks(frames: bytes, sample_rate: int):
    bytes_per_chunk = sample_rate * 2 * CHUNK_MS // 1000
    for i in range(0, len(frames), bytes_per_chunk):
        yield frames[i : i + bytes_per_chunk]
        await asyncio.sleep(CHUNK_MS / 1000)


async def process_segment(client, seg: str, source: str, target: str, t0: float, rows: list, audio_parts: dict, idx: int):
    t_commit = time.monotonic() - t0
    translated = await translate_segment(client, seg, source, target)
    t_translated = time.monotonic() - t0
    audio = await synthesize_segment(client, translated, target)
    t_audio = time.monotonic() - t0
    audio_parts[idx] = audio
    rows.append((idx, seg, translated, t_commit, t_translated, t_audio))


async def main() -> None:
    wav_path = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures/kn_pickup.wav")
    with wave.open(str(wav_path), "rb") as wf:
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        duration = wf.getnframes() / sample_rate

    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)

    with open(wav_path, "rb") as f:
        source, _ = await detect_language(client, f)
    target = PAIR.get(source)
    if target is None:
        sys.exit(f"unsupported source language: {source}")
    print(f"{wav_path.name}: {duration:.1f}s audio | {source} -> {target}\n")

    segmenter = Segmenter()
    rows: list = []
    audio_parts: dict[int, bytes] = {}
    tasks: list[asyncio.Task] = []
    idx = 0
    t0 = time.monotonic()

    async for event, text in stream_partials(paced_chunks(frames, sample_rate), source, sample_rate):
        segments = []
        if event == "partial":
            segments = segmenter.feed(text)
        elif event == "final":
            segments = segmenter.flush(text)
        for seg in segments:
            print(f"[{time.monotonic() - t0:5.2f}s] committed: {seg!r}")
            tasks.append(asyncio.create_task(
                process_segment(client, seg, source, target, t0, rows, audio_parts, idx)))
            idx += 1

    await asyncio.gather(*tasks)

    rows.sort()
    print(f"\n{'seg':>3} {'commit':>7} {'transl':>7} {'audio':>7} {'t+tts':>6}  text")
    for i, seg, translated, tc, tt, ta in rows:
        print(f"{i:>3} {tc:6.2f}s {tt:6.2f}s {ta:6.2f}s {ta - tc:5.2f}s  {seg}  ->  {translated}")

    out = io.BytesIO()
    with wave.open(out, "wb") as wf_out:
        wf_out.setnchannels(1)
        wf_out.setsampwidth(2)
        wf_out.setframerate(sample_rate)
        for i in sorted(audio_parts):
            with wave.open(io.BytesIO(audio_parts[i]), "rb") as wf_in:
                wf_out.writeframes(wf_in.readframes(wf_in.getnframes()))
    Path("out.wav").write_bytes(out.getvalue())

    if rows:
        first_audio = min(r[5] for r in rows)
        avg_pipe = sum(r[5] - r[3] for r in rows) / len(rows)
        print(f"\naudio duration {duration:.1f}s | first translated audio ready at {first_audio:.2f}s"
              f" | avg commit->audio {avg_pipe:.2f}s | wrote out.wav")


if __name__ == "__main__":
    asyncio.run(main())
