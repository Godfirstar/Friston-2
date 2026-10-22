from datetime import datetime
from pathlib import Path

import cv2

from friston3.config import PHOTOS_DIR


class RealCamera:
    """真实摄像头适配器，负责打开、读取、保存和释放设备。"""

    def __init__(self, index=0, save_dir=None):
        self.index = index
        self.save_dir = (
            Path(save_dir)
            if save_dir is not None
            else PHOTOS_DIR
        )
        self.cap = None

    def open(self):
        """打开摄像头；如果已经打开则复用现有连接。"""
        if self.cap is not None and self.cap.isOpened():
            return True

        self.close()
        self.cap = cv2.VideoCapture(self.index)
        if not self.cap.isOpened():
            self.close()
            return False
        return True

    def read(self):
        """读取一帧；摄像头未打开或读取失败时返回 None。"""
        if self.cap is None or not self.cap.isOpened():
            return None
        success, frame = self.cap.read()
        return frame if success else None

    def close(self):
        """释放摄像头资源。重复调用也是安全的。"""
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def save_frame(self, frame):
        """将一帧图像保存为 JPG。"""
        if frame is None:
            return {"success": False, "error": "没有可保存的图像"}

        self.save_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self.save_dir / f"photo_{timestamp}.jpg"
        if not cv2.imwrite(str(path), frame):
            return {"success": False, "error": "保存照片失败"}
        return {"success": True, "path": str(path)}

    def capture(self):
        """执行一次普通拍照，并在结束后释放摄像头。"""
        print("[Device] 正在拍照...")
        if not self.open():
            return {
                "success": False,
                "error": f"无法打开摄像头（设备索引：{self.index}）",
            }

        try:
            frame = None
            # 连续读取几帧，使用最后一帧，减少刚打开设备时的异常画面。
            for _ in range(5):
                image = self.read()
                if image is not None:
                    frame = image

            if frame is None:
                return {"success": False, "error": "读取图像失败"}
            return self.save_frame(frame)
        finally:
            self.close()
