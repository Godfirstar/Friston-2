"""Persistent offline TTS producer for Friston-3.

Launched once in the existing friston_tts environment by voice_output.py.
Receives JSONL requests over stdin and streams SEGMENT events over stdout.
Only generates PCM files; never opens COM10 or loads the robot Agent.

This is a runtime worker, NOT a test script.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .local_tts import OfflineChineseVoice
from .tts_export import split_text, synthesize_part


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve() -> None:
    start = time.perf_counter()
    try:
        voice = OfflineChineseVoice()
    except Exception as exc:
        emit({"event": "fatal", "error": f"{type(exc).__name__}: {exc}"})
        return
    emit({"event": "ready", "model_load_s": round(time.perf_counter() - start, 3)})

    for raw in sys.stdin.buffer:
        if not raw.strip():
            continue
        request = None
        try:
            request = json.loads(raw.decode("utf-8", errors="strict"))
            if request.get("cmd") == "shutdown":
                break
            if request.get("cmd") != "speak":
                emit({"event": "error", "id": request.get("id"), "error": "Unknown command"})
                continue

            request_id = int(request["id"])
            source = request["text"]
            if not isinstance(source, str) or not 0 < len(source.strip()) <= 500:
                raise ValueError("Reply must contain between 1 and 500 characters")
            out_dir = Path(request["output_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            segment_chars = max(25, min(72, int(os.getenv("FRISTON_TTS_SEGMENT_CHARS", "72"))))
            parts = split_text(source, limit=segment_chars)
            if not parts:
                raise ValueError("No text after normalization")

            total = 0
            synthesized_s = 0.0
            for part in parts:
                started = time.perf_counter()
                segments = synthesize_part(voice, part)
                duration = time.perf_counter() - started
                synthesized_s += duration
                for chunk in segments:
                    if not chunk or len(chunk) % 2 or len(chunk) > 384000:
                        raise ValueError("Unexpected PCM buffer size")
                    filename = out_dir / f"segment_{total:03d}.pcm"
                    filename.write_bytes(chunk)
                    emit({
                        "event": "segment",
                        "id": request_id,
                        "seq": total,
                        "bytes": len(chunk),
                        "path": str(filename),
                        "synth_s": round(duration, 3),
                    })
                    total += 1
            emit({
                "event": "done", "id": request_id, "segments": total,
                "synth_total_s": round(synthesized_s, 3),
            })
        except Exception as exc:
            # Keep engine loaded if the current request fails.
            emit({"event": "error", "id": request.get("id") if isinstance(request, dict) else None,
                  "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    serve()
