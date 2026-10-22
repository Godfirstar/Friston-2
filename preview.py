import cv2

from friston3.hardware.camera import RealCamera
from friston3.vision.yunet_observer import YuNetObserver
from friston3.vision.visualization import draw_faces

from friston3.config import CAMERA_INDEX


def main():
    camera = RealCamera(index=CAMERA_INDEX)
    observer = YuNetObserver()

    if not camera.open():
        print(f"无法打开摄像头（设备索引：{CAMERA_INDEX}）")
        return

    print("Friston-3 视觉预览")
    print("Q: 退出 | S: 拍照 | O: 输出观测")

    try:
        while True:
            frame = camera.read()
            if frame is None:
                print("读取视频帧失败")
                break

            observation = observer.observe(frame)
            display = frame.copy()
            height, width = display.shape[:2]

            # 画出人脸位置分类的参考线。
            for ratio in (0.4, 0.6):
                y = int(height * ratio)
                cv2.line(display, (0, y), (width, y), (255, 255, 0), 1)

            draw_faces(display, observation, show_position=True)

            count = observation["face_count"]
            cv2.putText(
                display,
                f"Faces: {count} | S: Save | O: Observe | Q: Quit",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
            )
            cv2.imshow("Friston-3 Preview", display)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                print("[Photo]", camera.save_frame(frame))
            elif key == ord("o"):
                print("[Observation]", observation)
    finally:
        camera.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
