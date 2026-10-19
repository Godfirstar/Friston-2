import cv2

from friston2.vision.composition import CompositionController
from friston2.vision.visualization import draw_faces


class AutoPhotoSkill:
    """实时分析摄像头画面，构图稳定后倒计时拍照。"""

    name = "auto_photo"

    parameters = {
        "countdown_seconds": {
            "type": int,
            "default": 3,
            "min": 1,
            "max": 10
        }
    }

    def __init__(self, camera, observer=None):
        self.camera = camera

        self.observer = observer

    def execute(self, countdown_seconds=3):
        # 每次执行任务，都使用新的状态机。
        controller = CompositionController(
            countdown_seconds=countdown_seconds
            )
        if not self.camera.open():
            return {
                "success": False,
                "message": "无法打开摄像头",
            }

        print("[AutoPhoto] 自动摄影已启动，按 Q 取消")
        result = {
            "success": False,
            "message": "用户取消自动拍照",
        }

        try:
            while True:
                frame = self.camera.read()
                if frame is None:
                    result["message"] = "摄像头读取失败"
                    break

                observation = self.observer.observe(frame)
                state = controller.update(observation)

                # 在副本上绘制提示，保存照片时仍使用未标注的原始帧。
                display = frame.copy()
                height, width = display.shape[:2]
                cv2.rectangle(
                    display,
                    (int(width * 0.40), int(height * 0.30)),
                    (int(width * 0.60), int(height * 0.60)),
                    (0, 200, 0),
                    1,
                )

                draw_faces(display, observation, show_center=True)

                cv2.putText(
                    display,
                    state["stage"],
                    (12, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (255, 255, 255),
                    2,
                )
                cv2.putText(
                    display,
                    state["hint"],
                    (12, 60),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    2,
                )
                cv2.putText(
                    display,
                    "Q: cancel",
                    (12, height - 15),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    2,
                )

                cv2.imshow("Friston-2 Auto Photo", display)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break

                if state["ready"]:
                    saved = self.camera.save_frame(frame)
                    if saved["success"]:
                        result = {
                            "success": True,
                            "path": saved["path"],
                            "message": f"自动拍照成功：{saved['path']}",
                        }
                    else:
                        result = {
                            "success": False,
                            "message": saved["error"],
                        }
                    break

        except Exception as error:
            result = {
                "success": False,
                "message": f"自动拍照异常：{error}",
            }
        finally:
            self.camera.close()
            cv2.destroyAllWindows()

        return result
