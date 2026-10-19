
import cv2

from friston2.config import YUNET_MODEL_PATH


class YuNetObserver:
    """基于 YuNet 的人脸观测器。"""

    def __init__(self, score_threshold=0.75):
        model_path = YUNET_MODEL_PATH

        if not model_path.is_file():
            raise FileNotFoundError(f"模型不存在: {model_path}")

        if model_path.stat().st_size < 200_000:
            raise ValueError("模型文件异常，可能下载到了 LFS 指针")

        self.detector = cv2.FaceDetectorYN.create(
            model=str(model_path),
            config="",
            input_size=(320, 320),
            score_threshold=score_threshold,
            nms_threshold=0.3,
            top_k=5000
        )

    def observe(self, frame):
        height, width = frame.shape[:2]

        # 让模型输入尺寸与实际画面匹配
        self.detector.setInputSize((width, height))

        _, detections = self.detector.detect(frame)

        faces = []

        if detections is not None:
            for det in detections:
                # YuNet 前四项为 x, y, w, h
                x1 = max(0, round(float(det[0])))
                y1 = max(0, round(float(det[1])))
                x2 = min(width, round(float(det[0] + det[2])))
                y2 = min(height, round(float(det[1] + det[3])))

                w = x2 - x1
                h = y2 - y1

                if w <= 0 or h <= 0:
                    continue

                cx = (x1 + w / 2) / width
                cy = (y1 + h / 2) / height

                horizontal = (
                    "left" if cx < 0.4
                    else "right" if cx > 0.6
                    else "center"
                )

                vertical = (
                    "up" if cy < 0.4
                    else "down" if cy > 0.6
                    else "center"
                )

                faces.append({
                    "bbox": [x1, y1, w, h],
                    "center_x": round(cx, 3),
                    "center_y": round(cy, 3),
                    "horizontal": horizontal,
                    "vertical": vertical,
                    "score": round(float(det[14]), 3)
                })

        return {
            "frame_width": width,
            "frame_height": height,
            "face_count": len(faces),
            "faces": faces
        }
