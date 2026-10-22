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


def split_text(text: str, limit: int = 38) -> list[str]:
    """Prefer phrase boundaries; bound each segment for <=12s playback."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    # Split after Chinese sentence and comma punctuation, retaining punctuation.
    parts = re.findall(r"[^，,。！？!?；;]+[，,。！？!?；;]?", text)
    if not parts:
        parts = [text]
    chunks: list[str] = []
    current = ""
    for part in parts:
        if len(current) + len(part) <= limit:
            current += part
            continue
        if current.strip():
            chunks.append(current.strip())
        while len(part) > limit:
            chunks.append(part[:limit].strip())
            part = part[limit:]
        current = part
    if current.strip():
        chunks.append(current.strip())
    return [p for p in chunks if p]


def synthesize_part(voice: OfflineChineseVoice, text: str) -> list[bytes]:
    try:
        return [voice.pcm16(text)]
    except ValueError as exc:
        # Some unusually slow speech can exceed the device's 12-second cap
        # even when the source text contains fewer than 38 characters.
        if "longer than 12 seconds" not in str(exc) or len(text) <= 3:
            raise
        mid = len(text) // 2
        return synthesize_part(voice, text[:mid]) + synthesize_part(voice, text[mid:])


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
    for segment in split_text(text):
        pcm_files.extend(synthesize_part(voice, segment))
    if not pcm_files:
        parser.error("No audio generated")
    for index, pcm in enumerate(pcm_files):
        (output / f"segment_{index:03d}.pcm").write_bytes(pcm)
    print(f"Exported {len(pcm_files)} PCM segment(s)")


if __name__ == "__main__":
    main()
