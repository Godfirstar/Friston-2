"""Offline speech input from StackChan's microphone, through its COM owner.

The firmware captures PCM and detects the end of an utterance. SenseVoice runs
once per utterance on the PC's CPU, with one recognizer kept for this object's
lifetime. Neither recordings nor transcripts are written to disk here.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .face_events import StackChanFaceEvents


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} 必须是 {minimum}..{maximum} 之间的整数") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须是 {minimum}..{maximum} 之间的整数")
    return value


class StackChanSpeechInput:
    """Read one nonempty transcript without opening another serial connection.

    ``touch`` waits for a touch on the StackChan display before recording;
    ``vad`` listens for an energy threshold while this method is active.
    The main loop should call ``read`` only after its previous response and
    speaker playback finish. This implements half-duplex interaction.
    """

    SAMPLE_RATE = 16000

    def __init__(self, face: StackChanFaceEvents):
        self.face = face
        self.mode = os.getenv("FRISTON_MIC_MODE", "touch").strip().lower()
        if self.mode not in ("touch", "vad"):
            raise ValueError("FRISTON_MIC_MODE 只能设为 touch 或 vad")
        self.max_ms = _env_int("FRISTON_MIC_MAX_MS", 8000, 1000, 12000)
        self.silence_ms = _env_int("FRISTON_MIC_SILENCE_MS", 700, 200, 2000)
        self.threshold = _env_int("FRISTON_MIC_THRESHOLD", 350, 30, 10000)
        self.num_threads = _env_int("FRISTON_ASR_THREADS", 2, 1, 8)
        project_root = Path(__file__).resolve().parent.parent
        configured_dir = os.getenv("FRISTON_ASR_MODEL_DIR", "").strip().strip('"')
        self.model_dir = Path(configured_dir).expanduser() if configured_dir else (
            project_root / "models" / "sensevoice"
        )
        if not self.model_dir.is_absolute():
            self.model_dir = project_root / self.model_dir
        self._recognizer = None
        self._np = None
        self._load_lock = threading.Lock()
        self._closed = threading.Event()
        self.last_input_timing: dict[str, float] = {}

    def preload(self) -> None:
        """Validate the device and load the local model once; never download it."""
        if self._closed.is_set():
            raise RuntimeError("StackChan 语音输入已关闭")
        with self._load_lock:
            if self._recognizer is not None:
                return
            if not self.face.enabled:
                raise RuntimeError(
                    "栈酱语音输入需要 FRISTON_STACKCHAN_ENABLED=1；"
                    "使用键盘输入请设置 FRISTON_INPUT_MODE=text。"
                )

            tokens = self.model_dir / "tokens.txt"
            model = self.model_dir / "model.int8.onnx"
            if not model.is_file():
                model = self.model_dir / "model.onnx"
            if not model.is_file() or not tokens.is_file():
                raise FileNotFoundError(
                    f"未找到离线 SenseVoice 模型：{self.model_dir}。"
                    "请放入 model.int8.onnx（或 model.onnx）和 tokens.txt，"
                    "也可通过 FRISTON_ASR_MODEL_DIR 指向已有模型目录。"
                    "程序不会联网下载；临时使用键盘请设置 FRISTON_INPUT_MODE=text。"
                )
            if model.stat().st_size == 0 or tokens.stat().st_size == 0:
                raise ValueError(f"SenseVoice 模型或词表为空，请重新下载：{self.model_dir}")

            try:
                import numpy as np
                import sherpa_onnx
            except (ImportError, OSError) as error:
                raise RuntimeError(
                    "当前 Python 环境未能加载离线 ASR。请在运行 main.py 的环境执行："
                    "python -m pip install sherpa-onnx numpy。"
                    f"原始错误：{error}"
                ) from error

            info = self.face.get_info()
            if "MIC_PCM16_V1" not in info:
                raise RuntimeError(
                    "栈酱当前固件尚未提供 MIC_PCM16_V1；请先烧录本补丁的摄像头/麦克风固件。"
                    f"当前固件：{info}"
                )

            print("[StackChanASR] 加载本地 SenseVoice（CPU，只加载一次）...", flush=True)
            started = time.perf_counter()
            try:
                recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model),
                    tokens=str(tokens),
                    num_threads=self.num_threads,
                    sample_rate=self.SAMPLE_RATE,
                    provider="cpu",
                    language="zh",
                    use_itn=True,
                    debug=False,
                )
            except AttributeError as error:
                raise RuntimeError(
                    "当前 sherpa-onnx 缺少 SenseVoice API；"
                    "请运行 python -m pip install --upgrade sherpa-onnx。"
                ) from error
            except (RuntimeError, ValueError) as error:
                raise RuntimeError(
                    f"SenseVoice 加载失败，请确认 ONNX 模型和 tokens.txt 来自同一模型包：{error}"
                ) from error
            self._np = np
            self._recognizer = recognizer
            print(
                f"[StackChanASR] 已就绪：{model.name}，CPU {self.num_threads} 线程，"
                f"加载 {time.perf_counter() - started:.2f}s。",
                flush=True,
            )

    def read(self) -> str:
        """Wait for one utterance; keyboard interrupts remain visible to main."""
        self.preload()
        if self.mode == "touch":
            print("\n[StackChanMIC] 轻触栈酱屏幕后说话，停顿后自动提交；Ctrl+C 退出。", flush=True)
        else:
            print("\n[StackChanMIC] 正在等待说话，停顿后自动提交；Ctrl+C 退出。", flush=True)

        while not self._closed.is_set():
            capture_started = time.perf_counter()
            pcm = self.face.record_pcm(
                mode=self.mode,
                # VAD uses longer monitoring windows to avoid reopening the
                # audio hardware and discarding startup audio every second.
                # This is a maximum wait: speech returns as soon as it ends,
                # and Ctrl+C can cancel the active command at any time.
                wait_ms=1000 if self.mode == "touch" else 10000,
                max_ms=self.max_ms,
                silence_ms=self.silence_ms,
                threshold=self.threshold,
            )
            captured_at = time.perf_counter()
            if self._closed.is_set():
                break
            if not pcm:
                continue
            if not isinstance(pcm, bytes) or len(pcm) % 2:
                raise ValueError("栈酱返回了无效的 PCM16LE 麦克风数据")
            if len(pcm) > self.SAMPLE_RATE * 2 * 12:
                raise ValueError("栈酱录音超过协议允许的 12 秒上限")

            # Explicit little-endian PCM keeps the representation independent
            # of the host CPU. ASR expects mono float32 in [-1, 1).
            samples = self._np.frombuffer(pcm, dtype="<i2").astype(self._np.float32)
            samples *= 1.0 / 32768.0
            stream = self._recognizer.create_stream()
            stream.accept_waveform(self.SAMPLE_RATE, samples)
            self._recognizer.decode_stream(stream)
            text = stream.result.text.strip()
            asr_seconds = time.perf_counter() - captured_at
            self.last_input_timing = {
                "capture_and_transfer_s": captured_at - capture_started,
                "audio_s": len(pcm) / (self.SAMPLE_RATE * 2),
                "asr_s": asr_seconds,
            }
            if self._closed.is_set():
                break
            if not text or not any(character.isalnum() for character in text):
                print("[StackChanASR] 没有识别到有效文字，请再说一次。", flush=True)
                continue

            print(f"你（栈酱麦克风）: {text}", flush=True)
            print(
                f"[StackChanASR] 音频 {self.last_input_timing['audio_s']:.2f}s，"
                f"转写 {asr_seconds:.2f}s。",
                flush=True,
            )
            # No hard-coded name replacement or inferred speaker identity.
            return text

        raise EOFError("StackChan 语音输入已关闭")

    def close(self) -> None:
        """Stop accepting input; the caller owns and closes the COM scheduler."""
        self._closed.set()
