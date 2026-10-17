
import math
import time


class CompositionController:
    def __init__(
        self,
        stable_seconds=1.5,
        countdown_seconds=3.0,
        grace_seconds=0.35,
        alpha=0.35
    ):
        self.stable_seconds = stable_seconds
        self.countdown_seconds = countdown_seconds
        self.grace_seconds = grace_seconds
        self.alpha = alpha

        if not 0 < alpha <= 1:
            raise ValueError("alpha 必须在 (0, 1] 内")

        self.reset()

    def reset(self):
        """重置状态机和滤波器。"""
        self.stable_since = None
        self.countdown_since = None
        self.missing_since = None
        self.smoothed = None

    def _result(self, stage, hint, ready=False):
        return {
            "stage": stage,
            "hint": hint,
            "ready": ready
        }

    def _judge(self, cx, cy, ratio, margin=0.0):
        """根据人脸中心和尺寸判断构图。"""

        if cx < 0.40 - margin:
            return False, "Move face right"

        if cx > 0.60 + margin:
            return False, "Move face left"

        if cy < 0.30 - margin:
            return False, "Move face down"

        if cy > 0.60 + margin:
            return False, "Move face up"

        if ratio < 0.18 - margin:
            return False, "Move closer"

        if ratio > 0.45 + margin:
            return False, "Move back"

        return True, "Composition OK"

    def evaluate(self, observation):
        """保留 v0.4 的单帧构图评价接口。"""

        count = observation["face_count"]

        if count == 0:
            return False, "No face detected"

        if count != 1:
            return False, "Multiple faces"

        face = observation["faces"][0]

        return self._judge(
            face["center_x"],
            face["center_y"],
            face["bbox"][3] / observation["frame_height"]
        )

    def update(self, observation, now=None):
        now = time.monotonic() if now is None else now

        count = observation["face_count"]

        # 1. 暂时丢失人脸：暂停，不立即清零
        if count == 0:
            if self.stable_since is not None:
                if self.missing_since is None:
                    self.missing_since = now

                lost_time = now - self.missing_since

                if lost_time <= self.grace_seconds:
                    return self._result(
                        "HOLD",
                        "Face lost briefly; timer paused"
                    )

            self.reset()
            return self._result("ADJUST", "No face detected")

        # 2. 当前只支持单人拍照
        if count != 1:
            self.reset()
            return self._result("ADJUST", "Multiple faces")

        face = observation["faces"][0]

        raw = (
            face["center_x"],
            face["center_y"],
            face["bbox"][3] / observation["frame_height"]
        )

        # 3. 原始观测超出宽松范围，立即重置
        raw_ok, hint = self._judge(*raw, margin=0.06)

        if not raw_ok:
            self.reset()
            return self._result("ADJUST", hint)

        # 4. 如果刚从短暂丢失中恢复，补偿暂停时间
        if self.missing_since is not None:
            lost_time = now - self.missing_since

            if lost_time > self.grace_seconds:
                self.reset()
            else:
                if self.stable_since is not None:
                    self.stable_since += lost_time

                if self.countdown_since is not None:
                    self.countdown_since += lost_time

                self.missing_since = None

        # 5. EMA 平滑人脸中心及尺寸
        if self.smoothed is None:
            self.smoothed = raw
        else:
            self.smoothed = tuple(
                self.alpha * current
                + (1 - self.alpha) * previous
                for current, previous in zip(raw, self.smoothed)
            )

        # 6. 用平滑值进行正式构图判断
        ok, hint = self._judge(*self.smoothed)

        if not ok:
            self.reset()
            return self._result("ADJUST", hint)

        # 7. 构图连续稳定阶段
        if self.stable_since is None:
            self.stable_since = now

        elapsed = now - self.stable_since

        if elapsed < self.stable_seconds:
            remaining = self.stable_seconds - elapsed

            return self._result(
                "STABILIZING",
                f"Keep framing: {remaining:.1f}s"
            )

        # 8. 构图稳定，开始倒计时
        if self.countdown_since is None:
            self.countdown_since = now

        remaining = (
            self.countdown_seconds
            - (now - self.countdown_since)
        )

        if remaining > 0:
            return self._result(
                "COUNTDOWN",
                f"Photo in {math.ceil(remaining)}s"
            )

        # 9. 最终观测依然有效，允许拍照
        return self._result(
            "READY",
            "Taking photo",
            ready=True
        )
