"""Natural phrase segmentation shared by the resident TTS producer."""

from __future__ import annotations

import re

from .local_tts import OfflineCharacterVoice


def split_text(text: str, limit: int = 32, *, first_limit: int | None = None) -> list[str]:
    """Yield a short first phrase and natural later phrases without losing text.

    A complete short sentence need not fill half of a large chunk budget.
    English words/contractions, decimal numbers and dotted initials stay whole.
    """
    if limit < 1 or (first_limit is not None and first_limit < 1):
        raise ValueError("Segment limit must be positive")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    # Latin words (including don't / Friston-3) stay atomic; CJK characters can
    # form short phrases. Whitespace is retained between neighboring words.
    tokens = re.findall(
        r"\d+(?:[.:]\d+)+|(?:[A-Za-z]\.){2,}|[A-Za-z0-9]+(?:['’_-][A-Za-z0-9]+)*|\s+|.",
        text,
    )
    chunks, current = [], ""
    for token in tokens:
        budget = first_limit if not chunks and first_limit is not None else limit
        if len(current) + len(token) > budget and current.strip():
            chunks.append(current.strip())
            current = ""
        # Extremely long identifiers may exceed the segment budget; keep words
        # intact up to the model's cap, and bound pathological input above it.
        while len(token) > 140:
            chunks.append(token[:140])
            token = token[140:]
        current += token
        if token in "。！？!?；;，,." and len(current.strip()) >= min(6, budget):
            chunks.append(current.strip())
            current = ""
    if current.strip():
        chunks.append(current.strip())
    return [p for p in chunks if p]


def synthesize_part(voice: OfflineCharacterVoice, text: str) -> list[bytes]:
    # Long clips are split from the generated waveform near a quiet point.
    # Each phrase is synthesized exactly once; no expensive recursive reruns.
    return voice.pcm16_chunks(text)

