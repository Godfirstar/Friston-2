"""Generate PCM files inside the isolated friston_tts environment.

This is a production speech helper, not a test. It never accesses COM10.
The caller passes UTF-8 text on stdin and supplies an output directory.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .local_tts import OfflineChineseVoice


def split_text(text: str, limit: int = 48) -> list[str]:
    """Prefer punctuation; never bisect an ordinary English word/contraction."""
    if limit < 1:
        raise ValueError("Segment limit must be positive")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    # Latin words (including don't / Friston-3) stay atomic; CJK characters can
    # form short phrases. Whitespace is retained between neighboring words.
    tokens = re.findall(r"[A-Za-z0-9]+(?:['’_-][A-Za-z0-9]+)*|\s+|.", text)
    chunks, current = [], ""
    for token in tokens:
        if len(current) + len(token) > limit and current.strip():
            chunks.append(current.strip())
            current = ""
        # Extremely long identifiers may exceed the segment budget; keep words
        # intact up to the model's cap, and bound pathological input above it.
        while len(token) > 140:
            chunks.append(token[:140])
            token = token[140:]
        current += token
        if token in "。！？!?；;，," and len(current) >= max(8, limit // 2):
            chunks.append(current.strip())
            current = ""
    if current.strip():
        chunks.append(current.strip())
    return [p for p in chunks if p]


def synthesize_part(voice: OfflineChineseVoice, text: str) -> list[bytes]:
    # Long clips are split from the generated waveform near a quiet point.
    # Each phrase is synthesized exactly once; no expensive recursive reruns.
    return voice.pcm16_chunks(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare offline voice PCM segments")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    text = sys.stdin.buffer.read().decode("utf-8", errors="strict").strip()
    if not text:
        parser.error("Empty input text")
    if len(text) > 500:
        parser.error("This version supports up to 500 characters per reply")
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    voice = OfflineChineseVoice()
    pcm_files: list[bytes] = []
    for segment in split_text(voice.prepare_text(text)):
        pcm_files.extend(synthesize_part(voice, segment))
    if not pcm_files:
        parser.error("No audio generated")
    for index, pcm in enumerate(pcm_files):
        (output / f"segment_{index:03d}.pcm").write_bytes(pcm)
    print(f"Exported {len(pcm_files)} PCM segment(s)")


if __name__ == "__main__":
    main()
