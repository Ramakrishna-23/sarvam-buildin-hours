"""Bulbul v3 synthesis for clause segments → raw PCM (s16le mono).

REST with `output_audio_codec="wav"` per clause: segments are 5–10 words so
each call is fast, and WAV decodes with the stdlib (streaming TTS is mp3-only,
which would force an ffmpeg dependency on the hot path).
"""

from __future__ import annotations

import base64
import io
import wave

from sarvamai import AsyncSarvamAI

DIRECTION_VOICES = {"hi-IN": "amit", "kn-IN": "ritu"}  # fixed voice per target


async def synth_segment_pcm(
    client: AsyncSarvamAI,
    text: str,
    language_code: str,
    *,
    speaker: str | None = None,
    sample_rate: int = 16000,
    pace: float = 1.0,
) -> bytes:
    """Returns raw pcm_s16le mono audio at `sample_rate`."""
    resp = await client.text_to_speech.convert(
        text=text,
        language_code=language_code,
        speaker=speaker or DIRECTION_VOICES.get(language_code, "amit"),
        model="bulbul:v3",
        speech_sample_rate=sample_rate,
        output_audio_codec="wav",
        pace=pace,
    )
    wav_bytes = base64.b64decode("".join(resp.audios))
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1, "expected s16le mono"
        assert w.getframerate() == sample_rate, f"expected {sample_rate} Hz"
        return w.readframes(w.getnframes())
