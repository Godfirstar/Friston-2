"""Offline speech input from StackChan's microphone, through its COM owner.

The firmware captures PCM and detects the end of an utterance. The selected
recognizer runs once per utterance on the PC's CPU and stays loaded for this
object's lifetime. Neither recordings nor transcripts are written to disk here.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import closing
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip()
    if raw not in ("0", "1"):
        raise ValueError(f"{name} 只能设为 1 或 0")
    return raw == "1"


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
        self.backend = os.getenv("FRISTON_ASR_BACKEND", "sensevoice").strip().lower()
        if self.backend not in ("sensevoice", "seaco"):
            raise ValueError("FRISTON_ASR_BACKEND 只能设为 sensevoice 或 seaco")
        self.hotwords_enabled = self.backend == "seaco" and _env_bool(
            "FRISTON_ASR_HOTWORDS", True
        )
        self.seaco_quantize = self.backend == "seaco" and _env_bool(
            "FRISTON_SEACO_QUANTIZE", True
        )
        project_root = Path(__file__).resolve().parent.parent
        model_setting = (
            "FRISTON_SEACO_MODEL_DIR" if self.backend == "seaco" else "FRISTON_ASR_MODEL_DIR"
        )
        default_directory = "seaco_paraformer" if self.backend == "seaco" else "sensevoice"
        configured_dir = os.getenv(model_setting, "").strip().strip('"')
        self.model_dir = Path(configured_dir).expanduser() if configured_dir else (
            project_root / "models" / default_directory
        )
        if not self.model_dir.is_absolute():
            self.model_dir = project_root / self.model_dir
        self._recognizer = None
        self._np = None
        self._hotword_text = ""
        self.hotword_count = 0
        self._load_lock = threading.Lock()
        self._closed = threading.Event()
        self.last_input_timing: dict[str, float] = {}
        # Only the accepted utterance of the current read may identify its
        # speaker. A retry, interruption or typed console turn must not reuse it.
        self.last_audio_pcm: bytes = b""
        self.last_audio_rate: int = self.SAMPLE_RATE
        self.last_input_source: str = ""
        # Optional observer only schedules current-audio identity work. It
        # should return immediately so recognition can overlap ASR on the PC.
        self.audio_observer = None

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

            try:
                import numpy as np
            except (ImportError, OSError) as error:
                raise RuntimeError(
                    "当前 Python 环境未能加载 numpy。请在运行 main.py 的环境执行："
                    "python -m pip install numpy==1.26.4。"
                    f"原始错误：{error}"
                ) from error

            info = self.face.get_info()
            if "MIC_PCM16_V1" not in info:
                raise RuntimeError(
                    "栈酱当前固件尚未提供 MIC_PCM16_V1；请使用已支持摄像头/麦克风的固件版本。"
                    f"当前固件：{info}"
                )

            started = time.perf_counter()
            if self.backend == "seaco":
                recognizer, model_name = self._load_seaco()
            else:
                recognizer, model_name = self._load_sensevoice()
            self._np = np
            self._recognizer = recognizer
            print(
                f"[StackChanASR] 已就绪：{self.backend}/{model_name}，CPU {self.num_threads} 线程，"
                f"加载 {time.perf_counter() - started:.2f}s。",
                flush=True,
            )

    def _load_sensevoice(self):
        """Keep the existing SenseVoice model choice and recognition options."""
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
            import sherpa_onnx
        except (ImportError, OSError) as error:
            raise RuntimeError(
                "当前 Python 环境未能加载 SenseVoice。请在运行 main.py 的环境执行："
                f"python -m pip install sherpa-onnx numpy。原始错误：{error}"
            ) from error
        print(
            "[StackChanASR] 加载本地 SenseVoice（CPU，只加载一次）；此后端不启用原生热词。",
            flush=True,
        )
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
        return recognizer, model.name

    def _load_seaco(self):
        """Load an already exported model; the wrapper must never prepare one."""
        model_names = (
            ("model_quant.onnx", "model_eb_quant.onnx")
            if self.seaco_quantize else ("model.onnx", "model_eb.onnx")
        )
        required = (*model_names, "config.yaml", "am.mvn", "tokens.json")
        # funasr-onnx otherwise downloads a missing directory or exports missing
        # ONNX files. Validate every required file before its constructor runs.
        missing = [
            name for name in required
            if not (self.model_dir / name).is_file()
            or (self.model_dir / name).stat().st_size == 0
        ]
        if missing:
            raise FileNotFoundError(
                f"SeACo 离线模型不完整：{self.model_dir}；缺少或为空：{', '.join(missing)}。"
                "请按离线准备说明显式下载官方 SeACo 权重并导出 ONNX，"
                "将配套文件放在同一目录，或设置 FRISTON_SEACO_MODEL_DIR。"
                f"当前 FRISTON_SEACO_QUANTIZE={int(self.seaco_quantize)}。"
                "运行时不会下载、导出或切换模型；可设 FRISTON_ASR_BACKEND=sensevoice 回到已有模型。"
            )
        try:
            import yaml
            from funasr_onnx import SeacoParaformer
        except (ImportError, OSError) as error:
            raise RuntimeError(
                "当前 Python 环境未能加载 SeACo ONNX。请在运行 main.py 的环境执行："
                "python -m pip install funasr-onnx==0.4.3。"
                f"原始错误：{error}"
            ) from error

        try:
            with (self.model_dir / "config.yaml").open("r", encoding="utf-8") as handle:
                config = yaml.safe_load(handle)
            with (self.model_dir / "tokens.json").open("r", encoding="utf-8") as handle:
                tokens = json.load(handle)
        except (OSError, ValueError, yaml.YAMLError) as error:
            raise ValueError(f"SeACo 配套配置无法读取，请检查模型包：{error}") from error
        if not isinstance(config, dict) or config.get("model") != "SeacoParaformer":
            raise ValueError("config.yaml 必须来自官方 SeacoParaformer 模型，不能混用普通 Paraformer。")
        frontend = config.get("frontend_conf", {})
        if not isinstance(frontend, dict) or frontend.get("fs") != self.SAMPLE_RATE:
            raise ValueError("SeACo 模型必须使用 16000 Hz 前端，才能接收栈酱的 PCM。")
        if not isinstance(tokens, list) or not tokens or not all(isinstance(t, str) for t in tokens):
            raise ValueError("SeACo tokens.json 必须是同一模型包的非空 token 数组。")

        words = []
        if self.hotwords_enabled:
            try:
                from friston3.knowledge.speech_entities import hotwords
                from friston3.knowledge.terra_kb import connect

                with closing(connect()) as db:
                    words = hotwords(db, max_chars=10)
                if not words:
                    raise ValueError("知识库没有可用的人名热词")
                if not isinstance(words, list) or any(
                    not isinstance(word, str) or not word or len(word) > 10
                    or any(c.isspace() for c in word) for word in words
                ):
                    raise ValueError("热词必须是 1..10 个字符且不含空白的名称列表")
            except Exception as error:
                raise RuntimeError(
                    "SeACo 热词读取失败。请确认补丁文件完整，并退出主程序后执行 "
                    "python -m friston3.knowledge.terra_kb build 重建知识库；"
                    "如需暂时关闭热词，请显式设置 FRISTON_ASR_HOTWORDS=0。"
                    f"原始错误：{error}"
                ) from error
            # Keep every returned name. OOV characters follow the official
            # wrapper's <unk> behavior, but report the limitation at startup.
            vocabulary = set(tokens)
            oov_names = [word for word in words if any(c not in vocabulary for c in word)]
            if oov_names:
                print(
                    f"[StackChanASR] {len(oov_names)} 个热词含模型字表外字符，仍传入完整词表；"
                    "这些字符会按官方接口映射为 <unk>，不能保证原字输出。示例："
                    + "、".join(oov_names[:5])
                    + ("……" if len(oov_names) > 5 else ""),
                    flush=True,
                )
        self._hotword_text = " ".join(words)
        self.hotword_count = len(words)

        class LocalSeaco(SeacoParaformer):
            """Correct the wrapper's empty-word entry, caching tokenization only."""

            def proc_hotword(self, text):
                if getattr(self, "_prepared_text", None) != text:
                    ids, lengths = super().proc_hotword(text)
                    if not text:
                        # 0.4.3 splits "" into an empty word (length -1) plus
                        # the official trailing sos/null row. Keep only null.
                        ids, lengths = ids[-1:], lengths[-1:]
                    self._prepared_text = text
                    self._prepared_words = (ids, lengths)
                return self._prepared_words

        print(
            f"[StackChanASR] 加载本地 SeACo-Paraformer（CPU，只加载一次），"
            f"原生热词{'开启' if self.hotwords_enabled else '关闭'}：{len(words)} 个。",
            flush=True,
        )
        try:
            recognizer = LocalSeaco(
                model_dir=str(self.model_dir),
                batch_size=1,
                device_id="-1",
                quantize=self.seaco_quantize,
                intra_op_num_threads=self.num_threads,
            )
            # This only maps text to IDs. Neither ASR nor a model warm-up runs.
            # The wrapper logs each OOV character separately; our bounded
            # startup summary already reports that limitation. Suppress only
            # those duplicate messages during this one-time tokenization.
            def keep_non_oov_logs(record):
                return not record.getMessage().startswith("oov character ")

            wrapper_logger = logging.getLogger("funasr_onnx")
            wrapper_logger.addFilter(keep_non_oov_logs)
            try:
                recognizer.proc_hotword(self._hotword_text)
            finally:
                wrapper_logger.removeFilter(keep_non_oov_logs)
        except Exception as error:
            raise RuntimeError(
                "SeACo 加载失败；请确认 funasr-onnx==0.4.3 已安装，"
                f"两个 ONNX 和配置来自同一次导出。不会自动回退：{error}"
            ) from error
        return recognizer, model_names[0]

    def _transcribe(self, samples) -> str:
        if self.backend == "sensevoice":
            stream = self._recognizer.create_stream()
            stream.accept_waveform(self.SAMPLE_RATE, samples)
            self._recognizer.decode_stream(stream)
            return stream.result.text.strip()

        try:
            results = self._recognizer(samples, self._hotword_text)
        except Exception as error:
            raise RuntimeError(f"SeACo 转写失败，未切换其他模型：{error}") from error
        if not results:
            return ""
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            raise RuntimeError("SeACo 返回了非预期结果；请使用 funasr-onnx==0.4.3。")
        prediction = results[0].get("preds")
        # The official no-timestamp path returns (text, tokens); the timestamp
        # path returns text directly. Never turn the whole tuple into a string.
        if isinstance(prediction, (tuple, list)) and prediction:
            prediction = prediction[0]
        if not isinstance(prediction, str):
            raise RuntimeError("SeACo 结果缺少文字 preds；请检查模型与运行库版本。")
        return prediction.strip()

    def read(self) -> str:
        """Wait for one utterance; keyboard interrupts remain visible to main."""
        self.clear_last_audio()
        self.last_input_timing = {}
        self.preload()
        if self.mode == "touch":
            print("\n[StackChanMIC] 轻触栈酱屏幕后说话，停顿后自动提交；Ctrl+C 进入电脑控制台。", flush=True)
        else:
            print("\n[StackChanMIC] 正在等待说话，停顿后自动提交；Ctrl+C 进入电脑控制台。", flush=True)

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
            if callable(self.audio_observer):
                try:
                    self.audio_observer(pcm, self.SAMPLE_RATE)
                except Exception as error:
                    print(f"[OwnerIdentity] 本轮身份观察未启动：{error}", flush=True)
            text = self._transcribe(samples)
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
            self.last_audio_pcm = pcm
            self.last_audio_rate = self.SAMPLE_RATE
            self.last_input_source = "speech"
            return text

        raise EOFError("StackChan 语音输入已关闭")

    def clear_last_audio(self) -> None:
        """Release the in-memory clip, also before a local typed fallback."""
        self.last_audio_pcm = b""
        self.last_audio_rate = self.SAMPLE_RATE
        self.last_input_source = ""

    def close(self) -> None:
        """Stop accepting input; the caller owns and closes the COM scheduler."""
        self._closed.set()
        self.clear_last_audio()
