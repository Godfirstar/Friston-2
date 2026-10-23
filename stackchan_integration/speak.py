"""Manual Friston-3 StackChan speaker command-line utility (not a test file).

  python -m stackchan_integration.speak --beep
  python -m stackchan_integration.speak --text "博士，我准备好了。"
  python -m stackchan_integration.speak --wav path\\to\\example16k.wav

Close main.py first; the USB/TCP device link has one controller. Close Arduino's
serial monitor too when using USB. Offline Wi-Fi settings follow the main app.
"""

from __future__ import annotations

import argparse
import array
import math
import os
import sys
import wave
from pathlib import Path

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
    mode.add_argument("--text", help="Synthesize Chinese/English locally and play through StackChan")
    mode.add_argument("--wav", help="Play a previously generated 16k mono 16-bit WAV")
    parser.add_argument("--port", default=None, help="USB port or tcp://192.168.4.1:8765")
    parser.add_argument("--transport", choices=("serial", "tcp"), default=None)
    parser.add_argument("--host", default=None, help="Numeric IP for offline Wi-Fi")
    parser.add_argument("--tcp-port", type=int, default=None)
    parser.add_argument("--volume", type=int, default=80, help="Speaker volume, 0..160; start low")
    parser.add_argument("--speed", type=float, default=None, help="TTS speed 0.7..1.3 (default: FRISTON_TTS_SPEED or 1.0)")
    parser.add_argument("--speaker", type=int, help="Kokoro speaker ID, 0..102; Chinese male 58..102")
    parser.add_argument("--style", choices=("friston", "natural"), help="Light electronic colour or original stock voice")
    parser.add_argument("--model-dir", help="Override offline TTS model location")
    args = parser.parse_args()

    try:
        if args.beep:
            pcm = pcm_beep()
        elif args.wav:
            pcm = load_wav(args.wav)
        else:
            from .local_tts import OfflineChineseVoice
            if args.speaker is not None:
                os.environ["FRISTON_TTS_SPEAKER"] = str(args.speaker)
            if args.style:
                os.environ["FRISTON_TTS_STYLE"] = args.style
            print("Loading offline character voice model...")
            voice = OfflineChineseVoice(model_dir=args.model_dir)
            print(f"Voice: {voice.backend}, speaker={voice.speaker}, style={voice.style}")
            if voice.warning:
                print(f"Note: {voice.warning}")
            print("Synthesizing locally... (the first run may take longer)")
            pcm = voice.pcm16(args.text, speed=args.speed)
        seconds = len(pcm) / 32000.0
        print(f"Audio ready: {seconds:.2f}s / {len(pcm)} bytes")
        transport = args.transport
        if args.port and not args.port.lower().startswith("tcp://") and transport is None:
            transport = "serial"
        with StackChanBridge(port=args.port, transport=transport, host=args.host,
                             tcp_port=args.tcp_port) as bridge:
            version = bridge.info()
            print(f"StackChan ({bridge.port}): {version}")
            bridge.set_volume(args.volume)
            print("Playing through StackChan's built-in speaker...")
            bridge.play_pcm(pcm)
            print("AUDIO DONE")
    except (ValueError, FileNotFoundError, OSError, RuntimeError, TimeoutError) as error:
        parser.exit(1, f"Speech unavailable: {error}\n")


if __name__ == "__main__":
    main()
