"""Manual speech playback through the same automatic connection and TTS path.

Close main.py before running this utility so one process owns the device.
"""
from __future__ import annotations

import argparse

from .face_events import StackChanFaceEvents
from .voice_output import AgentSpeechOutput


def main() -> None:
    parser = argparse.ArgumentParser(description="通过自动连接的栈酱朗读一段文字")
    parser.add_argument("--text", required=True, help="需要朗读的中文或英文")
    args = parser.parse_args()
    voice = None
    try:
        with StackChanFaceEvents() as device:
            if not device.wait_ready(timeout=8.0):
                raise ConnectionError("未发现栈酱；连接设备热点或插入 USB 后重试。")
            device.on_request()
            try:
                voice = AgentSpeechOutput(device)
                if not voice.enabled or voice.volume == 0:
                    raise RuntimeError("当前已静音，请在主程序说‘恢复声音’或输入 /voice on。")
                if not voice.speak(args.text):
                    raise RuntimeError("本次朗读未完成，请查看上方连接或语音提示。")
            finally:
                device.on_reply_ready()
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"朗读未完成：{error}\n")
    finally:
        if voice is not None:
            voice.close()


if __name__ == "__main__":
    main()
