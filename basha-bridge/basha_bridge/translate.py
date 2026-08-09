import re

from sarvamai import AsyncSarvamAI

DIGIT_RUN = re.compile(r"\d[\d\s-]*\d|\d")


async def translate_segment(
    client: AsyncSarvamAI, text: str, source: str, target: str
) -> str:
    """Translate one committed segment, protecting literal digit runs (OTPs,
    gate numbers) from semantic translation."""
    digits = DIGIT_RUN.findall(text)
    protected = text
    for i, d in enumerate(digits):
        protected = protected.replace(d, f"§{i}§", 1)

    resp = await client.text.translate(
        input=protected,
        source_language_code=source,
        target_language_code=target,
        model="mayura:v1",
        mode="modern-colloquial",
        output_script="fully-native",
    )
    out = resp.translated_text
    for i, d in enumerate(digits):
        out = out.replace(f"§{i}§", d)
    return out
