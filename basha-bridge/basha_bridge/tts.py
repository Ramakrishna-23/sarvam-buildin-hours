import base64

from sarvamai import AsyncSarvamAI

# bulbul:v3 speaker set only (v2 voices are rejected); one fixed voice per direction
VOICE_FOR_LANGUAGE = {"hi-IN": "amit", "kn-IN": "rohan"}


async def synthesize_segment(
    client: AsyncSarvamAI, text: str, language: str, sample_rate: int = 16000
) -> bytes:
    """Synthesize one segment, returning raw WAV bytes."""
    resp = await client.text_to_speech.convert(
        text=text,
        language_code=language,
        speaker=VOICE_FOR_LANGUAGE.get(language, "amit"),
        model="bulbul:v3",
        speech_sample_rate=sample_rate,
        output_audio_codec="wav",
    )
    return base64.b64decode("".join(resp.audios))
