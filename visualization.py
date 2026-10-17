import cv2


def draw_faces(display, observation, *, show_center=False, show_position=False):
    """在传入的画面副本上绘制人脸框，可选中心点或位置标签。"""
    height, width = display.shape[:2]

    for face in observation["faces"]:
        x, y, w, h = face["bbox"]
        cv2.rectangle(
            display,
            (x, y),
            (x + w, y + h),
            (0, 255, 0),
            2,
        )

        if show_center:
            cv2.circle(
                display,
                (
                    int(face["center_x"] * width),
                    int(face["center_y"] * height),
                ),
                4,
                (0, 0, 255),
                -1,
            )

        if show_position:
            position = f"{face['horizontal']}/{face['vertical']}"
            cv2.putText(
                display,
                position,
                (x, max(y - 8, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )

    return display
