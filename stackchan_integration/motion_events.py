"""Finite expressive presets on the existing USB/TCP connection owner.

The model chooses meaning, not motor coordinates. Firmware interpolates every
sequence and cancels it for sensor capture. No extra model call, connection or
thread is used here; an ACK confirms acceptance, never encoder-verified arrival.
"""
from __future__ import annotations

import logging
import time

from friston3.runtime_settings import SETTINGS
from .stackchan_bridge import StackChanCommandRejected

LOG = logging.getLogger(__name__)
_ALLOWED_SKILLS = {"take_photo", "auto_photo", "npc_talk", "control_volume", "configure_device"}
_EMOTION_FACES = {
    "neutral": "neutral", "warm": "happy", "amused": "happy",
    "proud": "proud", "thinking": "thinking", "curious": "doubt",
    "surprised": "surprised", "concerned": "sad", "sad": "sad",
    "firm": "angry", "sleepy": "sleepy",
}
_GESTURES = frozenset({
    "none", "auto", "nod", "agree", "shake", "tilt", "greet", "think",
    "proud", "chuckle", "surprised", "concerned", "bow", "look_owner",
    "look_left", "look_right", "scan",
})
# A little variation comes from the selected emotion and turn counter, never
# keyword matching in the reply. Neutral responses deliberately include rests.
_AUTO_GESTURES = {
    "neutral": ("nod", "none", "tilt", "none"),
    "warm": ("greet", "nod", "agree"),
    "amused": ("chuckle", "tilt"),
    "proud": ("proud", "nod"),
    "thinking": ("think", "tilt"),
    "curious": ("tilt", "think"),
    "surprised": ("surprised",),
    "concerned": ("concerned",),
    "sad": ("concerned", "bow"),
    "firm": ("nod",),
    "sleepy": ("settle",),
}
_LEGACY_GESTURES = {
    "none": "none", "nod": "nod", "agree": "nod", "shake": "shake",
    "tilt": "tilt", "greet": "nod", "think": "tilt", "proud": "nod",
    "chuckle": "nod", "surprised": "tilt", "concerned": "tilt", "bow": "nod",
    "look_left": "look_left", "look_right": "look_right", "scan": "shake",
    "attend": "tilt", "settle": "none", "beat": "none",
}


def reply_presentation(result) -> tuple[str, str]:
    """Accept only bounded metadata; task failures never add reply movement."""
    if not isinstance(result, dict) or result.get("success") is not True:
        return "sad", "none"
    skill = result.get("executed_skill")
    if skill in ("take_photo", "auto_photo"):
        return "happy", "agree"
    if skill in ("control_volume", "configure_device"):
        if (skill == "configure_device" and result.get("setting") == "owner_side"
                and result.get("choice") in ("left", "right")):
            return "happy", "look_owner"
        return "neutral", "nod"
    if skill != "npc_talk":
        return "neutral", "none"
    presentation = result.get("presentation")
    if not isinstance(presentation, dict):
        return "neutral", "none"
    emotion, gesture = presentation.get("emotion"), presentation.get("gesture")
    face = _EMOTION_FACES.get(emotion, "neutral") if isinstance(emotion, str) else "neutral"
    return face, gesture if isinstance(gesture, str) and gesture in _GESTURES else "none"


def gesture_token(gesture: str, *, emotion="neutral", variation=0) -> str:
    if not SETTINGS.get("gestures_enabled", True):
        return "none"
    if gesture == "auto":
        choices = _AUTO_GESTURES.get(emotion, _AUTO_GESTURES["neutral"])
        return choices[variation % len(choices)]
    if gesture == "look_owner":
        side = SETTINGS.get("owner_side", "none")
        return {"left": "look_left", "right": "look_right"}.get(side, "none")
    return gesture if gesture in _GESTURES - {"auto", "look_owner"} else "none"


def _result(device, **value):
    with device._cond:
        device.last_motion_result = dict(value)
    return value


def _send_preset(device, bridge, token, *, when="now"):
    """Only a complete GESTURE ERR can be ignored without losing framing."""
    capabilities = bridge.info()
    if "GESTURE_V2" in capabilities:
        selected = token
        placement = SETTINGS.get("placement", "shoulder")
        options = {"placement": placement, "when": when}
    elif "GESTURE_V1" in capabilities:
        selected = _LEGACY_GESTURES.get(token, "none")
        options = {}
        when = "now"
        if selected == "none" and token != "none":
            return _result(device, status="skipped", reason="preset_requires_v2", gesture=token)
    else:
        return _result(device, status="skipped", reason="gesture_firmware_required")
    try:
        bridge._check_active()
        bridge.gesture(selected, **options)
    except (StackChanCommandRejected, ValueError) as error:
        # A complete ERR line (or a local validation rejection) has a known
        # boundary. Preserve speech on that same healthy connection. Unknown
        # timeouts/transport errors must instead reach the owner for recovery.
        with device._cond:
            device._gesture_faulted = True
            device._reply_motion_allowed = False
        LOG.warning("表演预设被拒绝，已停用本连接的后续动作：%s", error)
        return _result(device, status="rejected", gesture=selected, error=str(error))
    print(f"[StackChanMotion] {selected} / {when} 已接受；到位未校验。")
    return _result(device, status="command_accepted", gesture=selected,
                   requested_gesture=token, when=when, verified=False)


def execute_state_gesture(device, bridge, job):
    """Best-effort state cue; a newer turn/phase makes it stale immediately."""
    with device._cond:
        if (job.session_epoch != device._connection_epoch
                or job.motion_turn != device._motion_turn
                or job.motion_revision != device._motion_revision
                or job.motion_phase != device._motion_phase
                or job.cancelled.is_set() or time.monotonic() >= job.deadline):
            return _result(device, status="skipped", reason="superseded_state")
        if (not SETTINGS.get("gestures_enabled", True) or device._gesture_faulted
                or device._motion_camera_active):
            return _result(device, status="skipped", reason="disabled_faulted_or_camera")
        token = job.arguments.get("gesture")
        if token not in {"attend", "think"}:
            return _result(device, status="skipped", reason="invalid_state_preset")
    return _send_preset(device, bridge, token)


def execute_reply_gesture(device, bridge, job):
    """One semantic sequence plus at most two small, spaced speech beats."""
    now = time.monotonic()
    audio = job.operation == "audio"
    with device._cond:
        if (job.session_epoch != device._connection_epoch
                or job.motion_turn != device._motion_turn
                or job.motion_revision != device._motion_revision
                or device._motion_phase != "reply" or not device._reply_prepared):
            return _result(device, status="skipped", reason="superseded_reply")
        first = device._reply_audio_segments == 0
        if audio:
            device._reply_audio_segments += 1
        if (not SETTINGS.get("gestures_enabled", True) or device._gesture_faulted
                or device._motion_camera_active):
            device._pending_gesture = "none"
            device._reply_motion_allowed = False
            return _result(device, status="skipped", reason="disabled_faulted_or_camera")
        if first:
            requested = device._pending_gesture
            device._pending_gesture = "none"
            token = gesture_token(requested, emotion=device._reply_emotion,
                                  variation=device._reply_variation)
            device._reply_motion_allowed = token != "none"
            if token == "none":
                if requested == "look_owner" and not getattr(device, "_owner_side_reported", False):
                    device._owner_side_reported = True
                    print("[StackChanMotion] 主人方向尚未设置，本轮不猜朝向。")
                return _result(device, status="skipped", reason="none_or_unset_owner_side")
            # Automatic embellishment has a pause between turns. Deliberately
            # selected presets still work after a short preceding THINK cue.
            if requested == "auto" and now < device._next_reply_motion_at:
                device._reply_motion_allowed = False
                return _result(device, status="skipped", reason="reply_cooldown")
            device._next_reply_motion_at = now + 4.0
            device._next_speech_beat_at = now + 4.5
        elif (audio and device._reply_motion_allowed and device._reply_beats < 2
              and now >= device._next_speech_beat_at
              and len(job.arguments.get("pcm", b"")) >= 38400):
            token = "beat"
            device._reply_beats += 1
            device._next_speech_beat_at = now + 4.5
        else:
            return _result(device, status="skipped", reason="rest_between_speech_beats")
    result = _send_preset(device, bridge, token, when="speech" if audio else "now")
    if first and result["status"] != "command_accepted":
        with device._cond:
            device._reply_motion_allowed = False
    return result


def execute_stop_motion(device, bridge, job):
    """Explicit stop works even after automatic motion has been disabled."""
    capabilities = bridge.info()
    if "GESTURE_V1" in capabilities or "GESTURE_V2" in capabilities:
        # NONE cancels pending speech and active presets without commandeering
        # the camera's I2C session. It does not start a return-to-home motion.
        return _send_preset(device, bridge, "none")
    if "SERVO_MOTION_V1" in capabilities and not device._motion_camera_active:
        bridge.stop_head()
        return _result(device, status="command_accepted", gesture="stop", verified=False)
    return _result(device, status="skipped", reason="no_active_preset_capability")


def on_task_completed(device, result) -> None:
    """Muted or unavailable speech still receives one immediate reply preset."""
    if (not device.enabled or not SETTINGS.get("gestures_enabled", True)
            or not isinstance(result, dict) or result.get("success") is not True
            or result.get("executed_skill") not in _ALLOWED_SKILLS):
        return
    with device._cond:
        if device._pending_gesture == "none":
            return
    try:
        device._submit("reply_gesture", timeout=4.0)
    except Exception as error:
        # No replay after an uncertain ACK. The device owner already handles
        # link recovery; this decoration cannot overwrite the task's result.
        LOG.warning("表演动作未确认，保留原任务结果：%s", error)
