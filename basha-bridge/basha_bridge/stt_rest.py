from typing import BinaryIO

from sarvamai import AsyncSarvamAI


async def detect_language(
    client: AsyncSarvamAI, audio_file: BinaryIO
) -> tuple[str, str]:
    """Batch STT with unknown language -> (language_code, transcript)."""
    resp = await client.speech_to_text.transcribe(
        file=audio_file, model="saaras:v3", language_code="unknown"
    )
    return resp.language_code or "unknown", resp.transcript or ""
