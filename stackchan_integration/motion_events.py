"""Bounded expressive gestures on the existing USB/TCP owner worker.

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
_EMOTION_FACES = {
    "neutral": "neutral", "warm": "happy", "amused": "happy",
    "proud": "proud", "thinking": "thinking", "curious": "doubt",
    "surprised": "surprised", "concerned": "sad", "sad": "sad",
    "firm": "angry", "sleepy": "sleepy",
}
_GESTURES = frozenset({"none", "nod", "shake", "tilt", "look_owner"})


def reply_presentation(result) -> tuple[str, str]:
    """Validate model metadata again at the physical-device boundary.

    Content is never classified by keyword. The model selects a closed enum;
    raw angles, commands and stage directions are not accepted here.
    """
    if not isinstance(result, dict) or result.get("success") is not True:
        return "sad", "none"
    skill = result.get("executed_skill")
    if skill in ("take_photo", "auto_photo"):
        return "happy", "nod"
    if skill != "npc_talk":
        return "neutral", "none"
    presentation = result.get("presentation")
    if not isinstance(presentation, dict):
        return "neutral", "none"
    emotion = presentation.get("emotion")
    gesture = presentation.get("gesture")
    face = _EMOTION_FACES.get(emotion, "neutral") if isinstance(emotion, str) else "neutral"
    return face, gesture if isinstance(gesture, str) and gesture in _GESTURES else "none"


def gesture_token(gesture: str) -> str:
    if os.getenv("FRISTON_STACKCHAN_GESTURES", "1").strip().lower() in _OFF:
        return "none"
    if gesture == "look_owner":
        # LEFT/RIGHT are the robot's perspective, not the viewer's. A shoulder
        # scene alone does not identify which side the wearer is standing on.
        side = os.getenv("FRISTON_OWNER_SIDE", "none").strip().lower()
        return {"left": "look_left", "right": "look_right"}.get(side, "none")
    return gesture if gesture in {"nod", "shake", "tilt"} else "none"


def execute_reply_gesture(device, bridge, job):
    """Consume at most one preset per reply, without sleeping during audio.

    The firmware owns a short finite sequence. It cancels the sequence when
    camera/microphone capture begins; the host never opens a second COM port.
    """
    with device._cond:
        requested = getattr(device, "_pending_gesture", "none")
        device._pending_gesture = "none"
        token = gesture_token(requested)
        if token == "none":
            if (requested == "look_owner" and os.getenv("FRISTON_STACKCHAN_GESTURES", "1").strip().lower() not in _OFF
                    and not getattr(device, "_owner_side_reported", False)):
                device._owner_side_reported = True
                print("[StackChanMotion] 未配置主人相对方向，保留表情；FRISTON_OWNER_SIDE 可设 left 或 right。")
            return {"status": "skipped", "reason": "none_disabled_or_unset_side"}
        if getattr(device, "_gesture_faulted", False):
            return {"status": "skipped", "reason": "previous_motion_error"}
        if getattr(device, "_motion_camera_active", False):
            print("[StackChanMotion] 摄像头会话占用中，本轮跳过动作。")
            return {"status": "skipped", "reason": "camera_session_active"}
        if time.monotonic() < getattr(device, "_next_gesture_at", 0.0):
            print("[StackChanMotion] 动作间隔不足 8 秒，本轮保留表情。")
            return {"status": "skipped", "reason": "cooldown"}
    if "GESTURE_V1" not in bridge.info():
        if not getattr(device, "_gesture_upgrade_reported", False):
            device._gesture_upgrade_reported = True
            print("[StackChanMotion] 当前固件没有肩部动作预设；烧录本次固件后启用。")
        return {"status": "skipped", "reason": "GESTURE_V1_required"}
    try:
        bridge._check_active()
        # Reserve before sending: an uncertain ACK must never cause a retry.
        with device._cond:
            device._next_gesture_at = time.monotonic() + 8.0
        bridge.gesture(token)
        print(f"[StackChanMotion] {token} 已交给设备执行；未校验实体到位。")
        return {"status": "command_accepted", "gesture": token, "verified": False}
    except Exception:
        device._gesture_faulted = True
        # Let the serial owner recover a failed transaction. The dialogue/task
        # result itself is not rewritten as a hardware success or failure.
        raise


def on_task_completed(device, result) -> None:
    """Dispatch any remaining gesture when speech is muted or unavailable."""
    if (not device.enabled
            or os.getenv("FRISTON_STACKCHAN_GESTURES", "1").strip().lower() in _OFF
            or not isinstance(result, dict)
            or result.get("success") is not True
            or result.get("executed_skill") not in _ALLOWED_SKILLS):
        return
    if getattr(device, "_pending_gesture", "none") == "none":
        return
    try:
        device._submit("reply_gesture", timeout=5.0)
    except Exception as error:
        # Do not retry a possibly executed hardware action or change task success.
        with device._cond:
            device._gesture_faulted = True
        LOG.warning("表演动作未确认，本次运行禁用自动动作；原任务结果保留：%s", error)


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
    parser.add_argument("--transport", choices=("serial", "tcp"), default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--tcp-port", type=int, default=None)
    args = parser.parse_args()
    try:
        transport = args.transport
        if args.port and not args.port.lower().startswith("tcp://") and transport is None:
            transport = "serial"
        with StackChanFaceEvents(port=args.port, transport=transport, host=args.host,
                                 tcp_port=args.tcp_port) as device:
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
