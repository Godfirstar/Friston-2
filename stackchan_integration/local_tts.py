"""Optional offline Chinese male TTS using a downloaded Sherpa-ONNX VITS voice.

Models and dependencies are NOT bundled with the integration ZIP.
This module is imported only when speech synthesis is requested.
"""

from __future__ import annotations

import os
from pathlib import Path


MODEL_NAME = "vits-zh-hf-fanchen-wnj"


def default_model_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        current = Path(base) / "Friston3" / "tts" / MODEL_NAME
        legacy = Path(base) / "Friston2" / "tts" / MODEL_NAME
    else:
        current = Path.home() / "Friston3Models" / "tts" / MODEL_NAME
        legacy = Path.home() / "Friston2Models" / "tts" / MODEL_NAME
    # Reuse an installed voice; renaming the project must not discard weights.
    # An incomplete new directory must not hide a working legacy installation.
    for folder in (current, legacy):
        if all((folder / name).is_file() for name in
               (f"{MODEL_NAME}.onnx", "lexicon.txt", "tokens.txt")):
            return folder
    return current


class OfflineChineseVoice:
    """Small CPU TTS; returns bytes of 16-kHz signed PCM16 for the bridge."""

    def __init__(self, model_dir: str | Path | None = None):
        folder = Path(model_dir or os.getenv("FRISTON_TTS_MODEL_DIR") or default_model_dir())
        model_file = folder / f"{MODEL_NAME}.onnx"
        lexicon = folder / "lexicon.txt"
        tokens = folder / "tokens.txt"
        needed = (model_file, lexicon, tokens)
        missing = [str(p) for p in needed if not p.is_file()]
        if missing:
            raise FileNotFoundError(
                "Offline male voice model missing. Please download the official "
                "sherpa-onnx model and extract to " + str(folder) + "\nMissing: "
                + ", ".join(missing)
            )

        try:
            import numpy as np
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "Please install TTS dependencies: python -m pip install sherpa-onnx numpy"
            ) from exc

        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(model_file),
                    lexicon=str(lexicon),
                    tokens=str(tokens),
                ),
                # Both ONNX operator pools use this setting. A small pool
                # keeps synthesis steady while the dialogue app is running.
                num_threads=min(6, max(2, (os.cpu_count() or 4) // 2)),
                provider="cpu",  # Leave the laptop GPU free for Qwen3.
                debug=False,
            ),
            rule_fsts=str(folder / "number.fst") if (folder / "number.fst").exists() else "",
            max_num_sentences=1,
        )
        if not config.validate():
            raise ValueError("Invalid TTS model folder. Check model/tokens/lexicon.")
        self._np = np
        self._sherpa = sherpa_onnx
        self._tts = sherpa_onnx.OfflineTts(config)

    def pcm16(self, text: str, *, speed: float = 1.1) -> bytes:
        text = text.strip()
        if not text:
            raise ValueError("Text is empty")
        if len(text) > 120:
            raise ValueError("For v0.4, speak short sentences (under 120 characters)")
        if not (0.7 <= speed <= 1.3):
            raise ValueError("Voice speed must be between 0.7 and 1.3")
        options = self._sherpa.GenerationConfig()
        options.sid = 0  # This Chinese male voice model has one speaker.
        options.speed = speed
        options.silence_scale = 0.2
        audio = self._tts.generate(text, options)
        if len(audio.samples) == 0:
            raise RuntimeError("TTS did not produce audio")
        if audio.sample_rate != 16000:
            raise RuntimeError(
                f"This device protocol requires 16 kHz, got {audio.sample_rate} Hz"
            )
        if len(audio.samples) > 16000 * 12:
            raise ValueError("Voice is longer than 12 seconds; shorten the sentence")
        samples = self._np.asarray(audio.samples, dtype=self._np.float32)
        samples = self._np.clip(samples, -1.0, 1.0)
        # zlib CRC in the bridge protects this little-endian PCM payload.
        return (samples * 32767.0).astype("<i2").tobytes()
