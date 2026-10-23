"""Local YuNet/SFace evidence for one enrolled owner.

Only ``extract`` returns an embedding, for an explicit enrollment operation.
``observe`` reports who may be visible, never who is speaking. A camera frame
cannot establish which person produced a microphone recording.
"""
from hashlib import sha256
import math
import os
from pathlib import Path
from threading import RLock
from time import perf_counter

from friston3.config import MODELS_DIR, PROJECT_ROOT, YUNET_MODEL_PATH


SFACE_FILENAME = "face_recognition_sface_2021dec.onnx"
SFACE_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/face_recognition_sface/" + SFACE_FILENAME
)


def _number(name, default, lower, upper):
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数值") from exc
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name} 必须在 {lower} 到 {upper} 之间")
    return value


class OwnerFaceRecognizer:
    """Lazy CPU inference; never download models or save camera images.

    Templates are lists of L2-normalized SFace vectors. Pass the ``model_id``
    saved during enrollment when observing, so changing weights requires
    re-enrollment. Thresholds are deployment settings, not calibrated accuracy.
    """

    def __init__(self, model_path=None):
        raw_path = model_path or os.getenv("FRISTON_OWNER_FACE_MODEL", "")
        self.model_path = Path(raw_path).expanduser() if raw_path else MODELS_DIR / SFACE_FILENAME
        if not self.model_path.is_absolute():
            self.model_path = PROJECT_ROOT / self.model_path
        self.threshold = _number("FRISTON_OWNER_FACE_THRESHOLD", .50, .3, .95)
        self.ambiguity_margin = _number("FRISTON_OWNER_FACE_MARGIN", .05, 0, .2)
        self.min_face_size = int(_number("FRISTON_OWNER_FACE_MIN_SIZE", 64, 32, 256))
        self.min_blur = _number("FRISTON_OWNER_FACE_MIN_SHARPNESS", 25, 0, 1000)
        self.detector_threshold = _number("FRISTON_OWNER_FACE_DETECT_THRESHOLD", .85, .5, .99)
        self._detector = None
        self._recognizer = None
        self._cv = None
        self._np = None
        self._load_error = ""
        self._load_attempted = False
        self._lock = RLock()
        self.model_id = ""

    def status(self):
        """Inspect local readiness without starting inference or network I/O."""
        reason = self._load_error
        if not reason:
            if not YUNET_MODEL_PATH.is_file():
                reason = f"缺少 YuNet 模型：{YUNET_MODEL_PATH}"
            elif not self.model_path.is_file():
                reason = f"缺少 SFace 模型：{self.model_path}"
        return {
            "status": "unavailable" if reason else "ready" if self._recognizer is not None else "configured",
            "reason": reason, "model_id": self.model_id,
            "threshold": self.threshold, "model_path": str(self.model_path),
        }

    def preload(self):
        """Load local weights once, optionally during startup."""
        with self._lock:
            self._ensure_loaded()
            return self.status()

    def _ensure_loaded(self):
        if self._recognizer is not None:
            return True
        if self._load_attempted:
            return False
        self._load_attempted = True
        try:
            import cv2
            import numpy as np

            for path, minimum in ((YUNET_MODEL_PATH, 200_000), (self.model_path, 1_000_000)):
                if not path.is_file():
                    raise FileNotFoundError(f"缺少本地人脸模型：{path}")
                if path.stat().st_size < minimum:
                    raise ValueError(f"模型文件过小，可能是 Git LFS 指针：{path}")
            detector = cv2.FaceDetectorYN.create(
                str(YUNET_MODEL_PATH), "", (320, 320),
                self.detector_threshold, .3, 5000,
                cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU,
            )
            recognizer = cv2.FaceRecognizerSF.create(
                str(self.model_path), "", cv2.dnn.DNN_BACKEND_OPENCV,
                cv2.dnn.DNN_TARGET_CPU,
            )
            digest = sha256()
            with self.model_path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            self.model_id = "sface-sha256:" + digest.hexdigest()
            self._cv, self._np = cv2, np
            self._detector, self._recognizer = detector, recognizer
            self._load_error = ""
            return True
        except Exception as exc:
            # Optional recognition must not take the existing dialogue loop down.
            self._load_error = f"人脸识别不可用（{type(exc).__name__}）：{exc}"
            return False

    def _result(self, status, reason="", **fields):
        result = {
            "status": status, "reason": reason, "model_id": self.model_id,
            "face_count": 0, "owner_visible": False, "score": None,
            "threshold": self.threshold, "quality": {},
        }
        result.update(fields)
        return result

    def _prepare(self, frame):
        np, cv = self._np, self._cv
        if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
                or frame.ndim != 3 or frame.shape[2] != 3
                or min(frame.shape[:2]) < 32):
            return None, self._result("invalid_frame", "需要有效的 BGR uint8 摄像头画面。")
        # Bound CPU work for PC cameras; QVGA/VGA StackChan frames stay intact.
        height, width = frame.shape[:2]
        if max(height, width) > 640:
            scale = 640 / max(height, width)
            frame = cv.resize(frame, (round(width * scale), round(height * scale)), interpolation=cv.INTER_AREA)
        frame = np.ascontiguousarray(frame)
        height, width = frame.shape[:2]
        self._detector.setInputSize((width, height))
        _, detections = self._detector.detect(frame)
        if detections is None or not len(detections):
            return None, self._result("no_face", "画面内未检测到人脸。")
        detections = [row.copy() for row in detections if np.isfinite(row).all()]
        if not detections:
            return None, self._result("no_face", "未取得有效的人脸位置。")
        detections.sort(key=lambda row: float(row[2] * row[3]), reverse=True)
        return (frame, detections), None

    def _feature(self, frame, detection):
        cv, np = self._cv, self._np
        height, width = frame.shape[:2]
        x, y, face_w, face_h = map(float, detection[:4])
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(width, int(x + face_w)), min(height, int(y + face_h))
        quality = {
            "bbox": [round(x), round(y), round(face_w), round(face_h)],
            "detection_score": round(float(detection[14]), 4),
            "face_min_pixels": round(min(face_w, face_h), 1),
        }
        if min(face_w, face_h) < self.min_face_size or x1 <= x0 or y1 <= y0:
            return None, quality, "人脸太小，请靠近摄像头。"
        visible = (x1 - x0) * (y1 - y0) / max(face_w * face_h, 1)
        if visible < .95:
            return None, quality, "人脸在画面边缘被截断，请面向摄像头。"
        landmarks = detection[4:14].reshape(5, 2)
        if (not ((landmarks[:, 0] >= 0) & (landmarks[:, 0] < width)).all()
                or not ((landmarks[:, 1] >= 0) & (landmarks[:, 1] < height)).all()):
            return None, quality, "人脸关键点不完整，请面向摄像头。"
        crop = cv.cvtColor(frame[y0:y1, x0:x1], cv.COLOR_BGR2GRAY)
        sharpness = float(cv.Laplacian(crop, cv.CV_64F).var())
        quality["sharpness"] = round(sharpness, 1)
        if sharpness < self.min_blur:
            return None, quality, "画面较模糊，请保持稳定并改善照明。"
        aligned = self._recognizer.alignCrop(frame, detection)
        embedding = np.asarray(self._recognizer.feature(aligned), dtype=np.float32).reshape(-1).copy()
        norm = float(np.linalg.norm(embedding))
        if len(embedding) != 128 or not np.isfinite(embedding).all() or norm < 1e-8:
            return None, quality, "未取得有效的 128 维 SFace 特征。"
        return embedding / norm, quality, ""

    def extract(self, frame):
        """Extract one owner enrollment sample; reject every multi-face frame."""
        started = perf_counter()
        with self._lock:
            if not self._ensure_loaded():
                return self._result("unavailable", self._load_error)
            try:
                prepared, error = self._prepare(frame)
                if error:
                    return error
                image, detections = prepared
                if len(detections) != 1:
                    return self._result("ambiguous", "录入时画面中只能有你一个人。", face_count=len(detections))
                embedding, quality, reason = self._feature(image, detections[0])
                if embedding is None:
                    return self._result("low_quality", reason, face_count=1, quality=quality)
                return self._result(
                    "ok", "单人样本已提取；未保存原始照片。", face_count=1,
                    quality=quality, embedding=embedding.tolist(),
                    elapsed_ms=round((perf_counter() - started) * 1000, 1),
                )
            except Exception as exc:
                return self._result("unavailable", f"人脸特征提取失败（{type(exc).__name__}）：{exc}")

    def observe(self, frame, templates, *, model_id=None):
        """Report visible owner evidence; never persist visitor embeddings.

        Scores within ``FRISTON_OWNER_FACE_MARGIN`` below the threshold remain
        ambiguous. More than one matching face is also ambiguous. A single
        match among several faces establishes presence only; callers must use
        separate speaker evidence before choosing a personal dialogue context.
        """
        started = perf_counter()
        with self._lock:
            if templates is None:
                return self._result("not_enrolled", "尚未录入主人人脸。")
            if not self._ensure_loaded():
                return self._result("unavailable", self._load_error)
            if model_id and model_id != self.model_id:
                return self._result("unavailable", "人脸模型已更换，请重新录入主人人脸。")
            np = self._np
            try:
                reference = np.asarray(templates, dtype=np.float32)
                if reference.size == 0:
                    return self._result("not_enrolled", "尚未录入主人人脸。")
                if reference.ndim == 1:
                    reference = reference.reshape(1, -1)
                if (reference.ndim != 2 or reference.shape[1] != 128
                        or not 1 <= len(reference) <= 32 or not np.isfinite(reference).all()):
                    return self._result("unavailable", "主人人脸模板格式异常，请重新录入。")
                norms = np.linalg.norm(reference, axis=1, keepdims=True)
                if (norms < 1e-8).any():
                    return self._result("unavailable", "主人人脸模板无效，请重新录入。")
                reference = reference / norms
                prepared, error = self._prepare(frame)
                if error:
                    return error
                image, detections = prepared
                matches = []
                qualities = []
                failures = []
                # Bound inference when a crowd fills the frame; retain the full
                # face count so an unprocessed face cannot imply a lone speaker.
                for index, detection in enumerate(detections[:4]):
                    embedding, quality, reason = self._feature(image, detection)
                    quality["face_index"] = index
                    qualities.append(quality)
                    if embedding is None:
                        failures.append(reason)
                        continue
                    score = float(np.clip(np.max(reference @ embedding), -1, 1))
                    matches.append((score, index))
                if not matches:
                    return self._result("low_quality", failures[0] if failures else "没有可比对的人脸。",
                                        face_count=len(detections), quality={"faces": qualities})
                matches.sort(reverse=True)
                score, face_index = matches[0]
                qualifying = sum(value >= self.threshold for value, _ in matches)
                if qualifying > 1:
                    status, reason, visible = "ambiguous", "多张人脸均接近主人模板，身份不确定。", False
                elif qualifying == 1:
                    visible = True
                    if len(detections) > 1:
                        status, reason = "ambiguous", "多人画面中有主人；仅能确认在场，不能确定说话人。"
                    else:
                        status, reason = "match", "画面中有一张人脸匹配主人模板。"
                elif score >= self.threshold - self.ambiguity_margin:
                    status, reason, visible = "ambiguous", "相似度接近阈值，暂不确认主人。", False
                else:
                    status, reason, visible = "no_match", "本帧未取得主人人脸匹配证据。", False
                return self._result(
                    status, reason, face_count=len(detections),
                    owner_visible=visible, score=round(score, 4),
                    matched_face_index=face_index if visible else None,
                    quality={"faces": qualities, "examined_count": len(qualities)},
                    elapsed_ms=round((perf_counter() - started) * 1000, 1),
                )
            except Exception as exc:
                return self._result("unavailable", f"人脸比对失败（{type(exc).__name__}）：{exc}")
