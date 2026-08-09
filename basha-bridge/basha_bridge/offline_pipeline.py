"""Offline harness: fixture WAV → realtime STT → chunker → Mayura → Bulbul.

Streams the file at real-time pace (the server's VAD runs on receive timing),
prints a per-segment latency table, and writes translated audio to out.wav.

    uv run python -m basha_bridge.offline_pipeline fixtures/kn_pickup.wav --tgt hi-IN
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import wave
from pathlib import Path

from sarvamai import AsyncSarvamAI

from . import config
from .pipeline import SAMPLE_RATE, DirectionPipeline
from .stt_realtime import SttSession

FRAME_MS = 100
TAIL_SILENCE_S = 1.0


FIXTURE_META = {
    "hi_otp.wav": {
        "lang": "hi-IN",
        "speaker": "priya",
        "text": "मेरा ओटीपी चार सात दो नौ है। मैं मैजेस्टिक के पास खड़ी हूँ।",
        "notes": "OTP digit-hold test: chunker waits for full digit run before commit",
    },
    "hi_pickup.wav": {
        "lang": "hi-IN",
        "speaker": "amit",
        "text": "भैया, मैं गेट नंबर दो पर हूँ, चाय की दुकान के पास। आप कहाँ हो?",
        "notes": "Pickup location + clause boundaries",
    },
    "kn_pickup.wav": {
        "lang": "kn-IN",
        "speaker": "rohan",
        "text": "ನಾನು ಮೆಜೆಸ್ಟಿಕ್ ಹತ್ತಿರ ಇದ್ದೀನಿ. ಗೇಟ್ ಎರಡು ಬಳಿ ಬನ್ನಿ, ಚಹಾ ಅಂಗಡಿ ಪಕ್ಕ.",
        "notes": "Kannada → Hindi offline harness",
    },
    "kn_eta.wav": {
        "lang": "kn-IN",
        "speaker": "ritu",
        "text": "ಐದು ನಿಮಿಷದಲ್ಲಿ ಬರ್ತೀನಿ, ಟ್ರಾಫಿಕ್ ಜಾಸ್ತಿ ಇದೆ. ಅಲ್ಲೇ ಇರಿ.",
        "notes": "Short ETA utterance",
    },
}


async def stream_run(path: Path, src: str, tgt: str, out_path: Path | None = None):
    """Async generator yielding bus events, ending with run.complete."""
    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)

    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1, (
            f"fixture must be {SAMPLE_RATE} Hz mono (got {w.getframerate()}/{w.getnchannels()}ch)"
        )
        pcm = w.readframes(w.getnframes())

    out_pcm: asyncio.Queue = asyncio.Queue()
    events: list[dict] = []
    event_q: asyncio.Queue[dict] = asyncio.Queue()
    got_final = asyncio.Event()

    async def on_emit(evt: dict) -> None:
        events.append(evt)
        await event_q.put(evt)
        if evt.get("tag") == "utterance.final":
            got_final.set()

    resolved_src = src if src != "auto" else FIXTURE_META.get(path.name, {}).get("lang", src)
    pipe = DirectionPipeline(
        client,
        direction_id=f"{resolved_src}→{tgt}",
        speaker_identity="fixture",
        emit=on_emit,
        out_pcm=out_pcm,
    )
    pipe.activate(resolved_src, tgt)
    pipe.start()

    stt_lang = resolved_src if resolved_src != "auto" else "auto"
    stt = SttSession(client, on_event=pipe.on_stt_event, language_code=stt_lang, name="offline")
    stt_task = asyncio.create_task(stt.run())

    t0 = time.monotonic()
    frame_bytes = SAMPLE_RATE * 2 * FRAME_MS // 1000

    yield {
        "tag": "run.start",
        "fixture": path.name,
        "src": resolved_src,
        "tgt": tgt,
        "duration_s": round(len(pcm) / 2 / SAMPLE_RATE, 2),
    }

    async def drain() -> list[dict]:
        out = []
        while not event_q.empty():
            out.append(event_q.get_nowait())
        return out

    for i in range(0, len(pcm), frame_bytes):
        await stt.send_audio(pcm[i : i + frame_bytes])
        await asyncio.sleep(FRAME_MS / 1000)
        for evt in await drain():
            yield evt
    for _ in range(int(TAIL_SILENCE_S * 1000 / FRAME_MS)):
        await stt.send_audio(b"\x00" * frame_bytes)
        await asyncio.sleep(FRAME_MS / 1000)
        for evt in await drain():
            yield evt
    await stt.flush()

    try:
        await asyncio.wait_for(got_final.wait(), timeout=10)
    except asyncio.TimeoutError:
        yield {"tag": "run.warn", "message": "no transcript.final within 10 s of end of audio"}

    await asyncio.sleep(0.2)
    while not pipe._seg_q.empty():
        await asyncio.sleep(0.2)
    await asyncio.sleep(3)

    for evt in await drain():
        yield evt

    stt.close()
    stt_task.cancel()
    pipe.stop()

    chunks = []
    while not out_pcm.empty():
        chunks.append(out_pcm.get_nowait()[0])
    audio_s = 0.0
    if chunks and out_path is not None:
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(b"".join(chunks))
        audio_s = sum(len(c) for c in chunks) / 2 / SAMPLE_RATE

    spoken = [e for e in events if e["tag"] == "segment.spoken"]
    translated = {e["seg"]: e for e in events if e["tag"] == "segment.translated"}
    latency_table = []
    for e in spoken:
        tr = translated.get(e["seg"], {})
        latency_table.append(
            {
                "seg": e["seg"],
                "reason": tr.get("reason", "?"),
                "src": tr.get("src", ""),
                "tgt": tr.get("tgt", ""),
                "translate_ms": tr.get("translate_ms"),
                "tts_ms": e["tts_ms"],
                "ear_ms": e["ear_ms"],
            }
        )

    yield {
        "tag": "run.complete",
        "wall_s": round(time.monotonic() - t0, 2),
        "segments": len(spoken),
        "audio_s": round(audio_s, 2),
        "out_path": str(out_path) if out_path and chunks else None,
        "latency_table": latency_table,
    }


async def run(path: Path, src: str, tgt: str, out_path: Path) -> int:
    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)

    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1, (
            f"fixture must be {SAMPLE_RATE} Hz mono (got {w.getframerate()}/{w.getnchannels()}ch)"
        )
        pcm = w.readframes(w.getnframes())

    out_pcm: asyncio.Queue = asyncio.Queue()
    events: list[dict] = []
    done = asyncio.Event()

    async def emit(evt: dict) -> None:
        events.append(evt)
        tag = evt["tag"]
        if tag == "utterance.partial":
            print(f"  ‥ partial u{evt['utt']}: {evt['text']!r}")
        elif tag == "utterance.final":
            print(f"  ■ final   u{evt['utt']} [{evt.get('lang')}]: {evt['text']!r}")
            done.set()
        elif tag == "segment.translated":
            print(
                f"  → commit({evt['reason']}) {evt['seg']}: {evt['src']!r}\n"
                f"    ⇒ {evt['tgt']!r}  ({evt['translate_ms']} ms)"
            )
        elif tag == "segment.spoken":
            print(
                f"  ♪ spoken {evt['seg']}: tts {evt['tts_ms']} ms · "
                f"ear-to-ear {evt['ear_ms']} ms · {evt['audio_s']} s audio"
            )

    pipe = DirectionPipeline(
        client, direction_id=f"{src}→{tgt}", speaker_identity="fixture",
        emit=emit, out_pcm=out_pcm,
    )
    pipe.activate(src, tgt)
    pipe.start()

    stt = SttSession(client, on_event=pipe.on_stt_event, language_code=src, name="offline")
    stt_task = asyncio.create_task(stt.run())

    t0 = time.monotonic()
    frame_bytes = SAMPLE_RATE * 2 * FRAME_MS // 1000
    print(f"streaming {path.name} ({len(pcm)/2/SAMPLE_RATE:.1f} s) at real-time pace…")
    for i in range(0, len(pcm), frame_bytes):
        await stt.send_audio(pcm[i : i + frame_bytes])
        await asyncio.sleep(FRAME_MS / 1000)
    for _ in range(int(TAIL_SILENCE_S * 1000 / FRAME_MS)):
        await stt.send_audio(b"\x00" * frame_bytes)
        await asyncio.sleep(FRAME_MS / 1000)
    await stt.flush()

    try:
        await asyncio.wait_for(done.wait(), timeout=10)
    except asyncio.TimeoutError:
        print("⚠ no transcript.final within 10 s of end of audio")

    # let translate+tts drain
    await asyncio.sleep(0.2)
    while not pipe._seg_q.empty():
        await asyncio.sleep(0.2)
    await asyncio.sleep(3)

    stt.close()
    stt_task.cancel()
    pipe.stop()

    chunks = []
    while not out_pcm.empty():
        chunks.append(out_pcm.get_nowait()[0])
    if chunks:
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(b"".join(chunks))
        print(f"\nwrote {out_path} ({sum(len(c) for c in chunks)/2/SAMPLE_RATE:.1f} s)")

    spoken = [e for e in events if e["tag"] == "segment.spoken"]
    translated = {e["seg"]: e for e in events if e["tag"] == "segment.translated"}
    print(f"\n{'seg':<6}{'reason':<10}{'translate':<11}{'tts':<8}{'ear-to-ear':<11}")
    for e in spoken:
        tr = translated.get(e["seg"], {})
        print(
            f"{e['seg']:<6}{tr.get('reason','?'):<10}"
            f"{tr.get('translate_ms','?'):<11}{e['tts_ms']:<8}{e['ear_ms']:<11}"
        )
    print(f"\ntotal wall: {time.monotonic()-t0:.1f} s · segments: {len(spoken)}")
    return 0 if spoken else 1


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("wav", type=Path)
    p.add_argument("--src", default="auto")
    p.add_argument("--tgt", default="hi-IN")
    p.add_argument("--out", type=Path, default=Path("out.wav"))
    a = p.parse_args()
    sys.exit(asyncio.run(run(a.wav, a.src, a.tgt, a.out)))


if __name__ == "__main__":
    main()
