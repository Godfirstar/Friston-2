from datetime import datetime
import os
from pathlib import Path

import cv2

from friston3.config import CAMERA_INDEX, CAMERA_SOURCE, PHOTOS_DIR


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

    def snapshot_for_identity(self, *, timeout=2.5):
        """Serial PC snapshot; native camera calls have no Python hard timeout."""
        if not self.open():
            raise RuntimeError("身份识别无法打开电脑摄像头")
        try:
            frame = None
            for _ in range(4):
                value = self.read()
                if value is not None:
                    frame = value
            if frame is None:
                raise RuntimeError("身份识别没有获取到电脑摄像头画面")
            return frame
        finally:
            self.close()

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


class StackChanCamera(RealCamera):
    """栈酱 GC0308 摄像头；所有 USB 请求交给同一个设备工作线程。

    自动摄影/预览按需取帧，不在闲聊时后台推流。普通拍照使用 VGA；
    实时画面默认 QVGA，可用 FRISTON_STACKCHAN_CAMERA_WIDTH=640 提高分辨率。
    """

    def __init__(self, device, save_dir=None):
        super().__init__(index="StackChan GC0308", save_dir=save_dir)
        self.device = device
        self.width = int(os.getenv("FRISTON_STACKCHAN_CAMERA_WIDTH", "320"))
        if self.width not in (320, 640):
            raise ValueError("FRISTON_STACKCHAN_CAMERA_WIDTH 只能设为 320 或 640")
        self._opened = False
        self._first_frame = None
        self.last_error = ""

    def _get_frame(self, width):
        import numpy as np

        jpeg = self.device.capture_jpeg(width=width)
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError("栈酱返回的 JPEG 无法解码")
        expected = (width * 3 // 4, width)
        if frame.shape[:2] != expected:
            raise RuntimeError(f"栈酱图像尺寸异常：{frame.shape[:2]}，预期 {expected}")
        return frame

    def snapshot_for_identity(self, *, timeout=2.5):
        """Fresh low-resolution frame; capture and I2C release are one job."""
        import numpy as np

        jpeg = self.device.capture_identity_jpeg(width=self.width, timeout=timeout)
        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None or frame.shape[:2] != (self.width * 3 // 4, self.width):
            raise RuntimeError("身份识别收到无效的栈酱画面")
        return frame

    def open(self):
        if self._opened:
            return True
        self.last_error = ""
        if not self.device.enabled:
            self.last_error = "栈酱摄像头需要 FRISTON_STACKCHAN_ENABLED=1"
            print(f"[StackChanCamera] {self.last_error}")
            return False
        try:
            # 开始时取一帧并缓存，第一轮 read 直接复用，避免重复取图。
            self._first_frame = self._get_frame(self.width)
            self._opened = True
            return True
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            self.last_error = str(error)
            print(f"[StackChanCamera] 打开失败：{error}")
            self._release_camera()
            return False

    def read(self):
        if not self._opened:
            return None
        if self._first_frame is not None:
            frame, self._first_frame = self._first_frame, None
            return frame
        try:
            return self._get_frame(self.width)
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            self.last_error = str(error)
            print(f"[StackChanCamera] 取帧失败：{error}")
            return None

    def _release_camera(self):
        try:
            self.device.close_camera()
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            print(f"[StackChanCamera] 释放设备未确认：{error}")

    def close(self):
        was_open = self._opened
        self._opened = False
        self._first_frame = None
        if was_open:
            # 归还设备内部 I2C 总线，使触屏、麦克风和扬声器可以继续工作。
            self._release_camera()

    def capture(self):
        print("[Device] 正在使用栈酱摄像头拍照（640×480）...")
        if not self.device.enabled:
            return {"success": False, "error": "栈酱设备已禁用"}
        try:
            # 一次有效 VGA 帧即可；不照搬电脑摄像头连续抓五帧的等待。
            return self.save_frame(self._get_frame(640))
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            self.last_error = str(error)
            return {"success": False, "error": f"栈酱拍照失败：{error}"}
        finally:
            self._opened = False
            self._first_frame = None
            self._release_camera()


def create_camera(device=None):
    """main 和 preview 共用同一来源选择，避免预览仍打开电脑摄像头。"""
    if CAMERA_SOURCE == "pc":
        return RealCamera(index=CAMERA_INDEX)
    if CAMERA_SOURCE == "stackchan":
        if device is None:
            raise ValueError("栈酱摄像头需要共享的 StackChanFaceEvents 实例")
        return StackChanCamera(device)
    raise ValueError("FRISTON_CAMERA_SOURCE 只能设为 stackchan 或 pc")
