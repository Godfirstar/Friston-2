"""StackChan camera on the shared automatically selected device connection."""
from datetime import datetime

import cv2

from friston3.config import PHOTOS_DIR


class StackChanCamera:
    """栈酱 GC0308 摄像头；USB/Wi-Fi 请求交给同一个设备工作线程。

    自动摄影/预览按需取帧，不在闲聊时后台推流。
    实时画面固定 QVGA，普通拍照固定 VGA。
    """

    def __init__(self, device):
        self.save_dir = PHOTOS_DIR
        self.device = device
        self.width = 320
        self._session_epoch = None
        self._opened = False
        self._first_frame = None
        self.last_error = ""

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
        if self._session_epoch != self.device.connection_epoch:
            self._opened = False
            self._first_frame = None
        if self._opened:
            return True
        self.last_error = ""
        if not self.device.enabled:
            self.last_error = "栈酱设备尚未就绪，正在自动连接"
            print(f"[StackChanCamera] {self.last_error}")
            return False
        try:
            # 开始时取一帧并缓存，第一轮 read 直接复用，避免重复取图。
            epoch = self.device.connection_epoch
            self._first_frame = self._get_frame(self.width)
            if epoch != self.device.connection_epoch:
                self._first_frame = None
                raise ConnectionError("取帧期间设备连接已改变，请重新开始拍照")
            self._opened = True
            self._session_epoch = epoch
            return True
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            self.last_error = str(error)
            print(f"[StackChanCamera] 打开失败：{error}")
            self._release_camera()
            return False

    def read(self):
        if self._session_epoch != self.device.connection_epoch:
            self._opened = False
            self._first_frame = None
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
            return {"success": False, "error": "栈酱设备尚未就绪，正在自动连接"}
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


def create_camera(device):
    """Main and preview share the same StackChan connection owner."""
    if device is None:
        raise ValueError("栈酱摄像头需要共享的 StackChanFaceEvents 实例")
    return StackChanCamera(device)
