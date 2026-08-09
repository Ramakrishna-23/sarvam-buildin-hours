"""Synthesize hi/kn fixture WAVs via Bulbul TTS. Run: uv run python scripts/make_fixtures.py"""

import asyncio
import base64
from pathlib import Path

from sarvamai import AsyncSarvamAI

from basha_bridge import config

FIXTURES = {
    "hi_pickup.wav": (
        "hi-IN",
        "amit",
        "भैया, मैं गेट नंबर दो पर हूँ, चाय की दुकान के पास। आप कहाँ हो?",
    ),
    "hi_otp.wav": (
        "hi-IN",
        "priya",
        "मेरा ओटीपी चार सात दो नौ है। मैं मैजेस्टिक के पास खड़ी हूँ।",
    ),
    "kn_pickup.wav": (
        "kn-IN",
        "rohan",
        "ನಾನು ಮೆಜೆಸ್ಟಿಕ್ ಹತ್ತಿರ ಇದ್ದೀನಿ. ಗೇಟ್ ಎರಡು ಬಳಿ ಬನ್ನಿ, ಚಹಾ ಅಂಗಡಿ ಪಕ್ಕ.",
    ),
    "kn_eta.wav": (
        "kn-IN",
        "ritu",
        "ಐದು ನಿಮಿಷದಲ್ಲಿ ಬರ್ತೀನಿ, ಟ್ರಾಫಿಕ್ ಜಾಸ್ತಿ ಇದೆ. ಅಲ್ಲೇ ಇರಿ.",
    ),
}

OUT_DIR = Path(__file__).parent.parent / "fixtures"


async def main() -> None:
    client = AsyncSarvamAI(api_subscription_key=config.SARVAM_API_KEY)
    OUT_DIR.mkdir(exist_ok=True)
    for name, (lang, speaker, text) in FIXTURES.items():
        resp = await client.text_to_speech.convert(
            text=text,
            language_code=lang,
            speaker=speaker,
            model="bulbul:v3",
            speech_sample_rate=16000,
            output_audio_codec="wav",
        )
        audio = base64.b64decode("".join(resp.audios))
        (OUT_DIR / name).write_bytes(audio)
        print(f"wrote fixtures/{name} ({len(audio)} bytes) [{lang} · {speaker}]")


if __name__ == "__main__":
    asyncio.run(main())
