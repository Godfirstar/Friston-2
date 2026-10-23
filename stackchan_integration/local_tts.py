"""Offline CPU speech: bilingual Kokoro, with the installed legacy VITS fallback.

The downloaded Kokoro voice is a stock male voice, not Friston-3's actor or a
voice clone. ``friston`` adds only a light electronic colour. Model downloads
are explicit (``python -m stackchan_integration.voice_model install``); normal
startup and synthesis never access the network.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path


MODEL_NAME = "vits-zh-hf-fanchen-wnj"  # Public legacy name kept for old installers.
BILINGUAL_MODEL_NAME = "kokoro-int8-multi-lang-v1_1"
_NAME = re.compile(r"(?<![A-Za-z0-9_])(?:Friston|Frinston|Firston)[\s\-\u2011\u2013\u2014]*3(?![A-Za-z0-9_])", re.I)
_BARE_NAME = re.compile(r"(?<![A-Za-z0-9_\-\u2011\u2013\u2014])(?:Friston|Frinston|Firston)(?![A-Za-z0-9_\-\u2011\u2013\u2014]|\s*\d)", re.I)
_CJK = re.compile(r"[\u3400-\u9fff]")


def model_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Friston3" / "tts" if base else Path.home() / "Friston3Models" / "tts"


def _model_candidates(name: str) -> tuple[Path, Path]:
    base = os.environ.get("LOCALAPPDATA")
    legacy = Path(base) / "Friston2" / "tts" if base else Path.home() / "Friston2Models" / "tts"
    return model_root() / name, legacy / name


def default_model_dir() -> Path:
    """Legacy directory resolver; project renames still reuse old weights."""
    for folder in _model_candidates(MODEL_NAME):
        if all((folder / name).is_file() for name in
               (f"{MODEL_NAME}.onnx", "lexicon.txt", "tokens.txt")):
            return folder
    return _model_candidates(MODEL_NAME)[0]


def kokoro_model_file(folder: Path) -> Path:
    for name in ("model.int8.onnx", "model.onnx"):
        if (folder / name).is_file():
            return folder / name
    return folder / "model.int8.onnx"


def kokoro_missing(folder: Path) -> list[str]:
    required = [kokoro_model_file(folder), *(folder / name for name in (
        "voices.bin", "tokens.txt", "lexicon-us-en.txt", "lexicon-zh.txt",
    ))]
    missing = [str(p) for p in required if not p.is_file() or p.stat().st_size == 0]
    for name in ("espeak-ng-data", "dict"):
        if not (folder / name).is_dir() or not any((folder / name).iterdir()):
            missing.append(str(folder / name))
    return missing


def default_bilingual_dir() -> Path:
    for name in (BILINGUAL_MODEL_NAME, "kokoro-multi-lang-v1_1"):
        for folder in _model_candidates(name):
            if not kokoro_missing(folder):
                return folder
    return model_root() / BILINGUAL_MODEL_NAME


def prepare_speech_text(text: str, *, bilingual: bool = True) -> str:
    """Pronunciation-only aliases. The displayed reply and dialogue stay intact."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    chinese_context = not bilingual or bool(_CJK.search(text))
    pronunciation = os.getenv("FRISTON_TTS_NAME_PRONUNCIATION", "").strip()
    if not pronunciation:
        pronunciation = "弗里斯顿三号" if chinese_context else "Friston three"
    text = _NAME.sub(lambda _: pronunciation, text)
    # The persona often calls itself simply Friston. Do not match the middle
    # of an identifier, a hyphenated word, or another numbered model name.
    bare = "弗里斯顿" if chinese_context else "Friston"
    return _BARE_NAME.sub(lambda _: bare, text)


def _number_setting(name: str, default: float, low: float, high: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


class OfflineCharacterVoice:
    """One resident model; output is always mono little-endian 16-kHz PCM16."""

    def __init__(self, model_dir: str | Path | None = None):
        requested = os.getenv("FRISTON_TTS_BACKEND", "auto").strip().lower()
        if requested not in ("auto", "kokoro", "legacy"):
            raise ValueError("FRISTON_TTS_BACKEND must be auto, kokoro or legacy")
        explicit = model_dir or os.getenv("FRISTON_TTS_MODEL_DIR", "").strip()
        if explicit:
            folder = Path(explicit).expanduser()
            backend = requested if requested != "auto" else (
                "kokoro" if (folder / "voices.bin").is_file() else "legacy"
            )
        else:
            candidate = default_bilingual_dir()
            backend = "kokoro" if requested == "kokoro" or (
                requested == "auto" and not kokoro_missing(candidate)
            ) else "legacy"
            folder = candidate if backend == "kokoro" else default_model_dir()
        self.backend = backend
        self.model_dir = folder
        self.bilingual = backend == "kokoro"
        self.warning = ""
        if not self.bilingual:
            self.warning = (
                "当前仍是旧中文音源；已修正本机名字的中文读法，但完整英文需要安装 Kokoro。"
                "运行 python -m stackchan_integration.voice_model install 后重启。"
            )

        try:
            import numpy as np
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError(
                "TTS dependencies missing: python -m pip install -U sherpa-onnx numpy scipy"
            ) from exc
        self._np, self._sherpa = np, sherpa_onnx
        self._signal = None
        self.style = os.getenv("FRISTON_TTS_STYLE", "friston").strip().lower()
        if self.style not in ("friston", "natural"):
            raise ValueError("FRISTON_TTS_STYLE must be friston or natural")
        # Keep legacy Chinese playback working before the optional scipy upgrade.
        try:
            from scipy import signal
            self._signal = signal
        except ImportError:
            if self.bilingual:
                raise RuntimeError("Kokoro 24k→16k conversion requires: python -m pip install scipy")
            if self.style == "friston":
                self.style = "natural"
                self.warning += " 未安装 scipy，暂用原始音色。"
        self.speed = _number_setting("FRISTON_TTS_SPEED", 1.0, 0.7, 1.3)
        self.fx_mix = _number_setting("FRISTON_TTS_FX_MIX", 0.12, 0.0, 0.3)
        threads = int(_number_setting("FRISTON_TTS_THREADS", min(4, max(2, (os.cpu_count() or 4) // 2)), 1, 8))

        if self.bilingual:
            missing = kokoro_missing(folder)
            if missing:
                raise FileNotFoundError(
                    "Bilingual Kokoro model incomplete. Run python -m "
                    "stackchan_integration.voice_model install. Missing: " + ", ".join(missing)
                )
            if not hasattr(sherpa_onnx, "OfflineTtsKokoroModelConfig"):
                raise RuntimeError("This sherpa-onnx is too old; run python -m pip install -U sherpa-onnx")
            voice_config = sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(kokoro_model_file(folder)), voices=str(folder / "voices.bin"),
                tokens=str(folder / "tokens.txt"), data_dir=str(folder / "espeak-ng-data"),
                lexicon=",".join(str(folder / n) for n in ("lexicon-us-en.txt", "lexicon-zh.txt")),
            )
            # Some releases infer this from lexicon; configure it when exposed.
            if hasattr(voice_config, "dict_dir"):
                voice_config.dict_dir = str(folder / "dict")
            model_config = sherpa_onnx.OfflineTtsModelConfig(
                kokoro=voice_config, num_threads=threads, provider="cpu", debug=False,
            )
            fst_names = ("phone-zh.fst", "date-zh.fst", "number-zh.fst")
            self.speaker = int(_number_setting("FRISTON_TTS_SPEAKER", 58, 0, 102))
        else:
            missing = [str(folder / n) for n in (f"{MODEL_NAME}.onnx", "lexicon.txt", "tokens.txt")
                       if not (folder / n).is_file()]
            if missing:
                raise FileNotFoundError(
                    "No installed voice. Run python -m stackchan_integration.voice_model install. "
                    "Legacy files also missing: " + ", ".join(missing)
                )
            model_config = sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(folder / f"{MODEL_NAME}.onnx"), lexicon=str(folder / "lexicon.txt"),
                    tokens=str(folder / "tokens.txt"),
                ), num_threads=threads, provider="cpu", debug=False,
            )
            fst_names = ("number.fst",)
            self.speaker = 0
        config = sherpa_onnx.OfflineTtsConfig(
            model=model_config,
            rule_fsts=",".join(str(folder / n) for n in fst_names if (folder / n).is_file()),
            max_num_sentences=1,
        )
        if not config.validate():
            raise ValueError("Invalid TTS configuration; check complete model files and sherpa-onnx version")
        self._tts = sherpa_onnx.OfflineTts(config)
        count = getattr(self._tts, "num_speakers", None)
        count = count() if callable(count) else count
        if isinstance(count, int) and not 0 <= self.speaker < count:
            raise ValueError(f"Speaker {self.speaker} does not exist in this model (speakers: {count})")
        self._colour_filter = self._signal.butter(
            2, (100, 4700), btype="bandpass", fs=16000, output="sos"
        ) if self._signal is not None and self.style == "friston" else None

    def prepare_text(self, text: str) -> str:
        return prepare_speech_text(text, bilingual=self.bilingual)

    def _synthesize_pcm(self, text: str, *, speed: float | None = None) -> bytes:
        text = self.prepare_text(text)
        if not text:
            raise ValueError("Text is empty")
        if len(text) > 160:
            raise ValueError("Speak short segments (under 160 characters)")
        if not self.bilingual and re.search(r"[A-Za-z]", text):
            # The old Mandarin model can silently omit letters; make that visible.
            raise ValueError("旧中文音源无法完整朗读英文，请先安装 Kokoro 双语音源；本轮保留文字。")
        speed = self.speed if speed is None else float(speed)
        if not math.isfinite(speed) or not 0.7 <= speed <= 1.3:
            raise ValueError("Voice speed must be between 0.7 and 1.3")
        options = self._sherpa.GenerationConfig()
        options.sid, options.speed, options.silence_scale = self.speaker, speed, 0.2
        audio = self._tts.generate(text, options)
        samples = self._np.array(audio.samples, dtype=self._np.float32, copy=True)
        rate = int(audio.sample_rate)
        if samples.ndim != 1 or samples.size == 0 or rate <= 0:
            raise RuntimeError("TTS did not produce valid mono audio")
        if not self._np.all(self._np.isfinite(samples)):
            raise RuntimeError("TTS produced invalid sample values")
        if rate != 16000:
            if self._signal is None:
                raise RuntimeError("Install scipy for sample-rate conversion")
            common = math.gcd(rate, 16000)
            # Anti-alias filtering matters when converting Kokoro's 24 kHz.
            samples = self._signal.resample_poly(samples, 16000 // common, rate // common)
        if self._colour_filter is not None and self.fx_mix > 0:
            coloured = self._signal.sosfilt(self._colour_filter, samples)
            carrier = self._np.sin(2.0 * self._np.pi * 46.0 * self._np.arange(len(samples)) / 16000.0)
            # Mostly dry voice: mild ring modulation, no echo or theatrical growl.
            samples = (1.0 - self.fx_mix) * samples + self.fx_mix * coloured * carrier
        peak = float(self._np.max(self._np.abs(samples)))
        if peak > 0.94:
            samples = samples * (0.94 / peak)
        # Short fades prevent clicks at independently synthesized clip joins.
        fade = min(80, len(samples) // 2)
        if fade:
            ramp = self._np.linspace(0.0, 1.0, fade)
            samples[:fade] *= ramp
            samples[-fade:] *= ramp[::-1]
        return (self._np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


    def pcm16(self, text: str, *, speed: float | None = None) -> bytes:
        pcm = self._synthesize_pcm(text, speed=speed)
        if len(pcm) > 384000:
            raise ValueError("Voice is longer than 12 seconds; shorten the sentence")
        return pcm

    def pcm16_chunks(self, text: str) -> list[bytes]:
        """Synthesize once, then split exceptional long clips near quiet samples.

        Normal phrase chunks fit one device command. This avoids synthesizing a
        too-long sentence again recursively merely to meet the transport cap.
        """
        pcm = self._synthesize_pcm(text)
        result = []
        while len(pcm) > 384000:
            values = self._np.frombuffer(pcm, dtype="<i2")
            # Find the quietest 10-ms window in the last 1.5 seconds permitted.
            start, end, window = 168000, 191840, 160
            region = values[start:end].astype(self._np.float32)
            blocks = region[:len(region) // window * window].reshape(-1, window)
            energy = self._np.mean(blocks * blocks, axis=1)
            block = int(self._np.argmin(energy))
            low = start + block * window
            # Prefer a zero crossing within that small quiet interval.
            cut = low + int(self._np.argmin(self._np.abs(values[low:low + window].astype(self._np.int32))))
            result.append(pcm[:cut * 2])
            pcm = pcm[cut * 2:]
        if pcm:
            result.append(pcm)
        return result


# Old imports remain valid in user scripts and the isolated TTS environment.
OfflineChineseVoice = OfflineCharacterVoice
