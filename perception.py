import cv2


class FaceObserver:
    """使用 OpenCV Haar Cascade 检测画面中的人脸。"""

    def __init__(self):
        model_path = (
            cv2.data.haarcascades
            + "haarcascade_frontalface_default.xml"
        )
        self.detector = cv2.CascadeClassifier(model_path)
        if self.detector.empty():
            raise RuntimeError("人脸检测模型加载失败")

    def observe(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        boxes = self.detector.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(60, 60),
        )

        height, width = frame.shape[:2]
        faces = []
        for x, y, w, h in boxes:
            x, y, w, h = int(x), int(y), int(w), int(h)
            cx = (x + w / 2) / width
            cy = (y + h / 2) / height

            if cx < 0.4:
                horizontal = "left"
            elif cx > 0.6:
                horizontal = "right"
            else:
                horizontal = "center"

            if cy < 0.4:
                vertical = "up"
            elif cy > 0.6:
                vertical = "down"
            else:
                vertical = "center"

            faces.append({
                "bbox": [x, y, w, h],
                "center_x": round(cx, 3),
                "center_y": round(cy, 3),
                "horizontal": horizontal,
                "vertical": vertical,
            })

        return {
            "frame_width": width,
            "frame_height": height,
            "face_count": len(faces),
            "faces": faces,
        }
