"""Optional local CAM++ speaker verification for the current utterance.

The owner controller persists enrollment vectors. This module keeps audio in
memory, returns no raw audio, and never downloads a model. A cosine score is a
similarity, not a probability; short/noisy/overlapping speech can be inconclusive.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path


def _number(name, default, minimum, maximum):
    try:
        value = float(os.getenv(name, str(default)))
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError
        return value
    except ValueError as error:
        raise ValueError(f"{name} 必须在 {minimum}..{maximum} 之间") from error


class OwnerVoiceRecognizer:
    """Compare enrolled owner vectors to one mono 16 kHz PCM16LE utterance.

    Constructing this object does not import torch/FunASR or load weights.
    ``preload()`` is suitable for startup after enrollment, or for enrollment
    itself. ``extract()`` can also load lazily. Failed loads are cached so a
    missing dependency does not repeatedly delay dialogue; restart after fixing.
    """

    SAMPLE_RATE = 16000

    def __init__(self, model_dir=None):
        root = Path(__file__).resolve().parent.parent
        configured = str(model_dir or os.getenv("FRISTON_OWNER_VOICE_MODEL_DIR", "")).strip().strip('"')
        self.model_dir = Path(configured).expanduser() if configured else root / "models" / "campplus"
        if not self.model_dir.is_absolute():
            self.model_dir = root / self.model_dir
        self.enabled = os.getenv("FRISTON_OWNER_VOICE", "1").strip() != "0"
        self.threshold = _number("FRISTON_OWNER_VOICE_THRESHOLD", 0.65, 0.1, 0.99)
        self.reject_threshold = _number("FRISTON_OWNER_VOICE_REJECT_THRESHOLD", 0.45, -1.0, 0.98)
        if self.reject_threshold >= self.threshold:
            raise ValueError("声纹拒绝阈值必须小于匹配阈值")
        self.min_duration = _number("FRISTON_OWNER_VOICE_MIN_SECONDS", 1.5, 1.0, 5.0)
        self.min_active = _number("FRISTON_OWNER_VOICE_MIN_ACTIVE", 1.0, 0.5, 4.0)
        self.num_threads = int(_number("FRISTON_OWNER_VOICE_THREADS", 2, 1, 8))
        self._model = None
        self._np = None
        self._load_error = ""
        self._model_id = ""
        self._embedding_size = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self.load_seconds = 0.0

    @property
    def model_id(self):
        return self._model_id

    def _model_files(self):
        config_path = self.model_dir / "config.yaml"
        weight_name = "campplus_cn_common.bin"
        metadata_path = self.model_dir / "configuration.json"
        if metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            if not isinstance(metadata, dict):
                raise ValueError("CAM++ configuration.json 格式错误")
            paths = metadata.get("file_path_metas", {})
            if not isinstance(paths, dict):
                raise ValueError("CAM++ file_path_metas 格式错误")
            weight_name = paths.get("init_param") or metadata.get("model_file") or weight_name
        if not isinstance(weight_name, str) or not weight_name:
            raise ValueError("CAM++ 权重文件名无效")
        weight_path = (self.model_dir / weight_name).resolve()
        # A local model package must not redirect loading to a URL or other tree.
        if not weight_path.is_relative_to(self.model_dir.resolve()):
            raise ValueError("CAM++ 权重必须位于本地模型目录中")
        for path in (config_path, weight_path):
            if not path.is_file() or path.stat().st_size == 0:
                raise FileNotFoundError(f"缺少本地 CAM++ 文件：{path}")
        if weight_path.stat().st_size < 1024:
            raise ValueError("CAM++ 权重文件不完整，可能下载成了 Git LFS 指针")
        return config_path, weight_path

    def status(self):
        """Describe local availability without importing a model or running it."""
        result = {
            "status": "ready" if self._model is not None else "unloaded",
            "loaded": self._model is not None,
            "model_id": self._model_id,
            "model_dir": str(self.model_dir),
            "threshold": self.threshold,
            "reject_threshold": self.reject_threshold,
            "load_s": round(self.load_seconds, 3),
        }
        if not self.enabled:
            result.update(status="disabled", reason="FRISTON_OWNER_VOICE=0")
        elif self._load_error:
            result.update(status="unavailable", reason=self._load_error)
        elif self._model is None:
            try:
                self._model_files()
            except (OSError, ValueError, TypeError) as error:
                result.update(status="unavailable", reason=str(error))
        return result

    def preload(self):
        """Load a validated local checkpoint on CPU once; no hub lookup."""
        if not self.enabled:
            return {"status": "disabled", "reason": "声纹识别已关闭", "model_id": ""}
        with self._load_lock:
            if self._model is not None:
                return {"status": "ok", "model_id": self._model_id}
            if self._load_error:
                return {"status": "unavailable", "reason": self._load_error, "model_id": ""}
            started = time.perf_counter()
            try:
                config_path, weight_path = self._model_files()
                import yaml
                import numpy as np
                from funasr import AutoModel

                config_bytes = config_path.read_bytes()
                config = yaml.safe_load(config_bytes.decode("utf-8-sig"))
                if not isinstance(config, dict) or config.get("model") != "CAMPPlus":
                    raise ValueError("config.yaml 必须来自 CAM++ / CAMPPlus 模型")
                model_conf = config.get("model_conf")
                if not isinstance(model_conf, dict):
                    raise ValueError("CAM++ 配置缺少 model_conf")
                if model_conf.get("output_level", "segment") != "segment":
                    raise ValueError("CAM++ 需使用 segment 输出")
                frontend = config.get("frontend_conf") or {}
                if not isinstance(frontend, dict) or frontend.get("fs", 16000) != 16000:
                    raise ValueError("CAM++ 需使用 16000 Hz 模型")
                embedding_size = model_conf.get("embedding_size", 192)
                if not isinstance(embedding_size, int) or not 32 <= embedding_size <= 4096:
                    raise ValueError("CAM++ embedding_size 无效")
                digest = hashlib.sha256(config_bytes)
                with weight_path.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(block)
                # Supplying model_conf bypasses FunASR's download_model path.
                # CAMPPlus.inference computes its own fbank; no tokenizer,
                # ASR/VAD/companion speaker model, or remote code is needed.
                model = AutoModel(
                    model="CAMPPlus", model_conf=model_conf,
                    init_param=str(weight_path), model_path=str(self.model_dir),
                    device="cpu", ncpu=self.num_threads,
                    disable_update=True, disable_pbar=True, disable_log=True,
                    log_level="ERROR", trust_remote_code=False,
                    ignore_init_mismatch=False,
                    frontend=None, tokenizer=None,
                    vad_model=None, punc_model=None, spk_model=None,
                )
                self._np = np
                self._model = model
                self._embedding_size = embedding_size
                self._model_id = "campplus:" + digest.hexdigest()
            except Exception as error:
                self._load_error = f"{type(error).__name__}: {error}"
                return {"status": "unavailable", "reason": self._load_error, "model_id": ""}
            finally:
                self.load_seconds = time.perf_counter() - started
        return {"status": "ok", "model_id": self._model_id}

    def extract(self, pcm_bytes, sample_rate=16000):
        """Return a normalized enrollment vector only for an adequate clip.

        The energy gate is not a speaker-count or overlap detector. Enrollment
        must still be captured with the owner speaking alone.
        """
        started = time.perf_counter()
        result = {"status": "insufficient_audio", "model_id": self._model_id}
        if sample_rate != self.SAMPLE_RATE:
            return {**result, "reason": "声纹输入必须是单声道 16000 Hz PCM16LE"}
        if not isinstance(pcm_bytes, (bytes, bytearray)) or not pcm_bytes or len(pcm_bytes) % 2:
            return {**result, "reason": "没有本轮有效录音"}
        duration = len(pcm_bytes) / (2 * sample_rate)
        result["duration_s"] = round(duration, 3)
        if duration < self.min_duration or duration > 12.0:
            return {**result, "reason": f"录音需为 {self.min_duration:g}..12 秒；短句不用于声纹确认"}
        try:
            import numpy as np
            samples = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
            clipped = float(np.mean(np.abs(samples) >= 0.99))
            rms = float(np.sqrt(np.mean(samples * samples)))
            frame_size = sample_rate // 50
            frames = samples[:len(samples) // frame_size * frame_size].reshape(-1, frame_size)
            energies = np.sqrt(np.mean(frames * frames, axis=1))
            # A modest absolute floor avoids calling silence/background hiss
            # speech. Keeping this explicit makes room calibration possible.
            active = energies >= max(0.006, float(np.max(energies)) * 0.10)
            active_s = float(np.sum(active)) / 50.0
            result.update(rms=round(rms, 5), active_s=round(active_s, 3),
                          clipped_fraction=round(clipped, 4),
                          quality_gate="energy_only")
            if rms < 0.004 or active_s < self.min_active:
                return {**result, "reason": "有效发声太短或音量过小，请正常说一句完整的话"}
            if clipped > 0.02:
                return {**result, "reason": "录音削波过多，请稍远离麦克风"}
            loaded = self.preload()
            if loaded["status"] != "ok":
                return {**result, **loaded}
            # Trim only leading/trailing low-energy frames, keeping natural
            # pauses and a 100 ms margin. Do not concatenate unrelated speech.
            indices = np.flatnonzero(active)
            begin = max(0, int(indices[0]) * frame_size - sample_rate // 10)
            end = min(len(samples), (int(indices[-1]) + 1) * frame_size + sample_rate // 10)
            speech = samples[begin:end].copy()
            with self._infer_lock:
                outputs = self._model.generate(input=speech, fs=sample_rate, batch_size=1)
            if not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], dict):
                raise ValueError("CAM++ 未返回单条声纹结果")
            vector = outputs[0].get("spk_embedding")
            if hasattr(vector, "detach"):
                vector = vector.detach().cpu().numpy()
            vector = np.asarray(vector, dtype=np.float32)
            if vector.shape != (1, self._embedding_size) or not np.isfinite(vector).all():
                raise ValueError("CAM++ 声纹向量形状或数值无效")
            vector = vector[0]
            norm = float(np.linalg.norm(vector))
            if norm <= 1e-8:
                raise ValueError("CAM++ 返回零向量")
            result.update(status="ok", model_id=self._model_id,
                          embedding=(vector / norm).tolist(),
                          inference_s=round(time.perf_counter() - started, 3),
                          reason="本轮录音声纹已提取；不包含活体或多人混说检测")
            return result
        except Exception as error:
            return {**result, "status": "error", "reason": f"{type(error).__name__}: {error}",
                    "inference_s": round(time.perf_counter() - started, 3)}

    def observe(self, pcm_bytes, templates, sample_rate=16000, *, model_id=None):
        """Match only this clip. Never return an enrollment vector in evidence.

        ``templates`` may be vectors or dictionaries with embedding/model_id.
        When passing plain vectors, also supply their shared ``model_id``.
        The mean of the best two references reduces dependence on one outlier.
        """
        if not isinstance(templates, (list, tuple)) or not templates:
            return {"status": "not_enrolled", "matched": False, "reason": "尚未录入主人声纹"}
        extracted = self.extract(pcm_bytes, sample_rate=sample_rate)
        vector = extracted.pop("embedding", None)
        result = {**extracted, "matched": False, "threshold": self.threshold,
                  "reject_threshold": self.reject_threshold}
        if extracted["status"] != "ok":
            return result
        if model_id is not None and model_id != self._model_id:
            return {**result, "status": "model_mismatch", "reason": "声纹模型已变更，请重新录入主人声纹"}
        np = self._np
        scores = []
        try:
            probe = np.asarray(vector, dtype=np.float32)
            for template in templates:
                if isinstance(template, dict):
                    if template.get("model_id") != self._model_id:
                        continue
                    reference = template.get("embedding")
                else:
                    if model_id != self._model_id:
                        continue
                    reference = template
                reference = np.asarray(reference, dtype=np.float32)
                if reference.shape != probe.shape or not np.isfinite(reference).all():
                    continue
                norm = float(np.linalg.norm(reference))
                if norm > 1e-8:
                    scores.append(float(np.clip(np.dot(probe, reference / norm), -1.0, 1.0)))
            if not scores:
                return {**result, "status": "model_mismatch", "reason": "没有与当前模型匹配的有效声纹模板"}
            best = sorted(scores, reverse=True)[:2]
            score = sum(best) / len(best)
            result.update(score=round(score, 5), reference_count=len(scores))
            if score >= self.threshold:
                result.update(status="match", matched=True, reason="本轮声纹与主人模板相符")
            elif score <= self.reject_threshold:
                result.update(status="mismatch", reason="本轮声纹与主人模板不相符")
            else:
                result.update(status="uncertain", reason="声纹相似度位于不确定区间")
            return result
        except (TypeError, ValueError) as error:
            return {**result, "status": "error", "reason": f"声纹模板无效：{error}"}
