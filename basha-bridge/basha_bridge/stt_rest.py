from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

from sarvamai import AsyncSarvamAI


@dataclass(frozen=True)
class LanguageDetection:
    """One utterance language detection result.

    `language_code` is the effective language used by the relay gate. We call
    text LID whenever STT produced a transcript; STT remains the fallback for
    very short utterances where text LID may be empty or disagree.
    """

    language_code: str
    transcript: str
    stt_language: str
    text_lid_language: str
    stt_probability: float | None = None


async def detect_language(
    client: AsyncSarvamAI, audio_file: BinaryIO
) -> tuple[str, str]:
    """Batch STT with unknown language -> (language_code, transcript)."""
    resp = await client.speech_to_text.transcribe(
        file=audio_file, model="saaras:v3", language_code="unknown"
    )
    return resp.language_code or "unknown", resp.transcript or ""


async def detect_language_with_text_lid(
    client: AsyncSarvamAI,
    audio_file: BinaryIO,
) -> LanguageDetection:
    """Detect utterance language with REST STT and text-LID confirmation."""
    resp = await client.speech_to_text.transcribe(
        file=audio_file,
        model="saaras:v3",
        language_code="unknown",
    )
    stt_language = resp.language_code or "unknown"
    transcript = resp.transcript or ""
    stt_probability = getattr(resp, "language_probability", None)

    text_lid_language = ""
    if transcript.strip():
        lid = await client.text.identify_language(input=transcript)
        text_lid_language = lid.language_code or ""

    # Use text-LID as a confirmation signal, but keep STT as the dominant
    # source because very short pickup-call utterances can be too terse for LID.
    language_code = stt_language
    if text_lid_language and text_lid_language == stt_language:
        language_code = text_lid_language

    return LanguageDetection(
        language_code=language_code,
        transcript=transcript,
        stt_language=stt_language,
        text_lid_language=text_lid_language,
        stt_probability=stt_probability,
    )
