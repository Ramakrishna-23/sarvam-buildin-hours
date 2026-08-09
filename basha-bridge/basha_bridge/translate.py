"""Mayura translation for committed clause segments, with digit shielding.

Numeric entities (OTPs, gate numbers, amounts) are swapped for placeholder
tokens before translation and restored verbatim afterwards, so the model can
never corrupt them.
"""

from __future__ import annotations

import re

from sarvamai import AsyncSarvamAI

_NUM_RUN = re.compile(r"[0-9०-९೦-೯]+(?:[\s\-][0-9०-९೦-೯]+)*")


def shield_numbers(text: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        token = f"⟦{len(mapping)}⟧"
        mapping[token] = m.group(0)
        return token

    return _NUM_RUN.sub(repl, text), mapping


def restore_numbers(text: str, mapping: dict[str, str]) -> str:
    for token, original in mapping.items():
        if token in text:
            text = text.replace(token, original)
        else:  # model dropped/mangled the token — append so info is never lost
            text = f"{text} {original}"
    return text


async def translate_segment(
    client: AsyncSarvamAI,
    text: str,
    source_language_code: str,
    target_language_code: str,
) -> str:
    shielded, mapping = shield_numbers(text)
    resp = await client.text.translate(
        input=shielded,
        source_language_code=source_language_code,
        target_language_code=target_language_code,
        model="mayura:v1",
        mode="modern-colloquial",
        output_script="fully-native",
        numerals_format="international",
    )
    return restore_numbers(resp.translated_text, mapping)
