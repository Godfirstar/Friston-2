"""Bounded success gestures on the existing serial-owner worker.

No LLM calls, no second port, no new background thread. Completion means the
command sequence was acknowledged, NOT that encoder feedback verified arrival.
"""
from __future__ import annotations

import logging
import os
import time

LOG = logging.getLogger(__name__)
_OFF = {"0", "false", "off", "no"}
_ALLOWED_SKILLS = {"take_photo", "auto_photo", "npc_talk"}
# Degrees * 10; the neutral pose was manually validated with firmware v3.
_NOD = ((0, 450, 1.2), (0, 400, 0.7), (0, 450, 0.7))


def on_task_completed(device, result) -> None:
    """Called once after a complete reply/playback or a completed photo task.

    This intentionally waits for the short gesture transaction before the main
    loop starts its next microphone request. It is not simultaneous audio/motion.
    """
    if (not device.enabled
            or os.getenv("FRISTON_STACKCHAN_GESTURES", "1").strip().lower() in _OFF
            or not isinstance(result, dict)
            or result.get("success") is not True
            or result.get("executed_skill") not in _ALLOWED_SKILLS):
        return
    with device._cond:
        now = time.monotonic()
        if (device._closed or device._jobs or device._active_job is not None
                or getattr(device, "_motion_camera_active", False)
                or getattr(device, "_gesture_faulted", False)
                or now < getattr(device, "_next_gesture_at", 0.0)):
            return
        device._next_gesture_at = now + 8.0
    try:
        outcome = device._submit("success_nod", timeout=10.0)
        if outcome.get("status") == "commands_sent":
            print("[StackChanMotion] 点头指令序列已发送，目标回到 (0°, 45°)；未校验到位。")
        else:
            LOG.info("StackChan gesture skipped: %s", outcome.get("reason"))
    except Exception as error:
        # Do not retry a possibly executed hardware action or change task success.
        with device._cond:
            device._gesture_faulted = True
        LOG.warning("点头未完成，本次运行禁用自动动作；原任务结果保留：%s", error)


def _wait(bridge, job, seconds: float) -> None:
    end = time.monotonic() + seconds
    while True:
        bridge._check_active()
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        job.cancelled.wait(min(0.05, remaining))


def execute_success_nod(bridge, job, *, camera_active: bool):
    """Only called on the existing serial worker, as ONE atomic queue item."""
    if camera_active:
        return {"status": "skipped", "reason": "camera_session_active"}
    # v3 MOVE can close the camera implicitly; never send it while a camera
    # session is active. The manager's flag is conservative after I/O errors.
    if "SERVO_TORQUE_FIX_V3" not in bridge.info():
        return {"status": "skipped", "reason": "verified_v3_firmware_required"}
    attempted_move = False
    try:
        for x, y, dwell in _NOD:
            bridge._check_active()
            attempted_move = True
            bridge.move_head(x, y, 200)
            _wait(bridge, job, dwell)
        return {"status": "commands_sent", "verified": False, "target": [0, 450]}
    except BaseException:
        # Cancellation is not an emergency-stop guarantee. Use the same serial
        # owner for a bounded best-effort STOP, then propagate the original error.
        if attempted_move:
            old_cancel, old_deadline = bridge._cancelled, bridge._deadline
            try:
                bridge._set_operation(None, time.monotonic() + 1.0)
                bridge.stop_head()
            except Exception as stop_error:
                LOG.warning("停止头部运动未确认，必要时物理断电：%s", stop_error)
            finally:
                bridge._set_operation(old_cancel, old_deadline)
        raise


def main() -> int:
    import argparse
    from .face_events import StackChanFaceEvents
    parser = argparse.ArgumentParser(description="手动触发一次已安装的成功点头动作，不启动 LLM/TTS。")
    parser.add_argument("--nod", action="store_true", required=True)
    parser.add_argument("--port", default=None)
    args = parser.parse_args()
    try:
        with StackChanFaceEvents(port=args.port) as device:
            result = device._submit("success_nod", timeout=10.0)
            print(result)
            print("commands_sent 仅代表命令序列发送完成；请观察实体点头与回正。")
            return 0 if result["status"] == "commands_sent" else 2
    except KeyboardInterrupt:
        print("已请求取消；若机械卡住请物理断电。")
        return 130
    except Exception as error:
        print(f"点头未完成：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
