"""Manual Friston-3 StackChan speaker command-line utility (not a test file).

  python -m stackchan_integration.speak --beep
  python -m stackchan_integration.speak --text "博士，我准备好了。"
  python -m stackchan_integration.speak --wav path\\to\\example16k.wav

Close main.py and Arduino's serial monitor first: COM10 has one owner.
"""

from __future__ import annotations

import argparse
import array
import math
import os
import sys
import wave
from pathlib import Path

import serial

from .stackchan_bridge import StackChanBridge


def pcm_beep() -> bytes:
    """A short quiet tune to establish that the StackChan speaker works."""
    rate = 16000
    values = array.array("h")
    for freq, duration in ((440, 0.28), (550, 0.28), (660, 0.38)):
        count = int(rate * duration)
        for i in range(count):
            envelope = min(1.0, i / (rate * 0.015), (count - i) / (rate * 0.02))
            values.append(int(6800 * max(0.0, envelope) * math.sin(2 * math.pi * freq * i / rate)))
        values.extend([0] * int(rate * 0.06))
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def load_wav(path: str) -> bytes:
    """Read uncompressed 16-kHz mono PCM16 WAV; reject other encodings."""
    with wave.open(str(Path(path).expanduser()), "rb") as audio:
        if (
            audio.getnchannels() != 1
            or audio.getsampwidth() != 2
            or audio.getframerate() != 16000
            or audio.getcomptype() != "NONE"
        ):
            raise ValueError("WAV must be uncompressed PCM16, mono, 16000 Hz")
        if audio.getnframes() > 16000 * 12:
            raise ValueError("WAV is longer than 12 seconds")
        return audio.readframes(audio.getnframes())


def main() -> None:
    parser = argparse.ArgumentParser(description="Play locally generated speech on StackChan")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--beep", action="store_true", help="Play a 1-second demo tune; no TTS model")
    mode.add_argument("--text", help="Synthesize Mandarin locally and play through StackChan")
    mode.add_argument("--wav", help="Play a previously generated 16k mono 16-bit WAV")
    parser.add_argument("--port", default=os.getenv("FRISTON_STACKCHAN_PORT", "COM10"))
    parser.add_argument("--volume", type=int, default=80, help="Speaker volume, 0..160; start low")
    parser.add_argument("--speed", type=float, default=1.0, help="TTS synthesis speed 0.7..1.3")
    parser.add_argument("--model-dir", help="Override offline TTS model location")
    args = parser.parse_args()

    try:
        if args.beep:
            pcm = pcm_beep()
        elif args.wav:
            pcm = load_wav(args.wav)
        else:
            from .local_tts import OfflineChineseVoice
            print("Loading offline Chinese male voice model...")
            voice = OfflineChineseVoice(model_dir=args.model_dir)
            print("Synthesizing locally... (the first run may take longer)")
            pcm = voice.pcm16(args.text, speed=args.speed)
        seconds = len(pcm) / 32000.0
        print(f"Audio ready: {seconds:.2f}s / {len(pcm)} bytes")
        with StackChanBridge(port=args.port) as bridge:
            version = bridge.info()
            print(f"StackChan: {version}")
            bridge.set_volume(args.volume)
            print("Playing through StackChan's built-in speaker...")
            bridge.play_pcm(pcm)
            print("AUDIO DONE - back to idle")
    except (ValueError, FileNotFoundError, OSError, RuntimeError, TimeoutError, serial.SerialException) as error:
        parser.exit(1, f"Speech unavailable: {error}\n")


if __name__ == "__main__":
    main()
