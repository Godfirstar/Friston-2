"""Friston-3 <-> StackChan K151 USB bridge.

Negotiate ADPCM4/ACK1024 for fast uploads; retain ACK128/PCM16 compatibility.
Every clip is CRC-checked by the device before playback.
Only one process may own COM10.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import zlib
from array import array
from typing import Optional

import serial


# Canonical IMA ADPCM step and index tables. Reset predictor/index each clip.
_IMA_STEPS = (
     7,     8,     9,    10,    11,    12,    13,    14,    16,
    17,    19,    21,    23,    25,    28,    31,    34,    37,
    41,    45,    50,    55,    60,    66,    73,    80,    88,
    97,   107,   118,   130,   143,   157,   173,   190,   209,
   230,   253,   279,   307,   337,   371,   408,   449,   494,
   544,   598,   658,   724,   796,   876,   963,  1060,  1166,
  1282,  1411,  1552,  1707,  1878,  2066,  2272,  2499,  2749,
  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,
  7132,  7845,  8630,  9493, 10442, 11487, 12635, 13899, 15289,
 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
)
_IMA_INDEX_DELTAS = (-1, -1, -1, -1, 2, 4, 6, 8,
                     -1, -1, -1, -1, 2, 4, 6, 8)


def _encode_ima_adpcm(pcm16le: bytes) -> bytes:
    """Encode signed little-endian 16-bit mono PCM as 4-bit IMA ADPCM.

    Format: predictor=0, index=0 at clip start, two low-first nibbles per
    byte, final high nibble padded with zero for odd sample counts.
    """
    if len(pcm16le) % 2 != 0:
        raise ValueError("Expected PCM16 samples")
    samples = array("h")
    samples.frombytes(pcm16le)
    if sys.byteorder != "little":
        samples.byteswap()
    encoded = bytearray((len(samples) + 1) // 2)
    predictor = 0
    step_index = 0
    for i, sample in enumerate(samples):
        step = _IMA_STEPS[step_index]
        delta = int(sample) - predictor
        code = 0
        if delta < 0:
            code = 8
            delta = -delta
        difference = step >> 3
        if delta >= step:
            code |= 4
            delta -= step
            difference += step
        half = step >> 1
        if delta >= half:
            code |= 2
            delta -= half
            difference += half
        quarter = step >> 2
        if delta >= quarter:
            code |= 1
            difference += quarter

        predictor += -difference if (code & 8) else difference
        predictor = min(32767, max(-32768, predictor))
        step_index += _IMA_INDEX_DELTAS[code]
        step_index = min(88, max(0, step_index))
        if (i & 1) == 0:
            encoded[i // 2] = code
        else:
            encoded[i // 2] |= code << 4
    return bytes(encoded)


class StackChanBridge:
    """Control an Arduino-flashed StackChan over its USB CDC serial port."""

    VALID_FACES = frozenset({
        "idle", "thinking", "speaking", "happy", "neutral", "doubt",
        "sad", "angry", "sleepy",
    })

    def __init__(self, port: Optional[str] = None, baudrate: int = 115200):
        self.port = port or os.getenv("FRISTON_STACKCHAN_PORT", "COM10")
        self.baudrate = baudrate
        self._serial: Optional[serial.Serial] = None
        self._firmware_info: Optional[str] = None
        self.last_audio_timing: dict[str, float | str] = {}

    def connect(self) -> "StackChanBridge":
        if self._serial is not None and self._serial.is_open:
            return self
        try:
            self._serial = serial.Serial(
                self.port, self.baudrate, timeout=0.25, write_timeout=1.0
            )
            # Some ESP32 USB CDC configurations reboot when the port opens.
            time.sleep(1.2)
            self._serial.reset_input_buffer()
            self._exchange("PING", "PONG", timeout=5.0)
            return self
        except Exception:
            self.close()
            raise

    def _read_response(self, command: str, timeout: float = 3.0) -> str:
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("StackChan not connected; call connect() first")
        if not command.isascii() or any(ch in command for ch in "\r\n"):
            raise ValueError("Commands must be one ASCII line")
        self._serial.write((command + "\n").encode("ascii"))
        self._serial.flush()
        deadline = time.monotonic() + timeout
        recently_seen = []
        while time.monotonic() < deadline:
            raw = self._serial.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line.startswith("ERR "):
                raise RuntimeError(f"StackChan rejected {command!r}: {line}")
            if (line == "PONG" or line.startswith("OK FACE ")
                    or line.startswith("OK VOLUME ") or line.startswith("INFO ")):
                return line
            if len(recently_seen) < 5:
                recently_seen.append(line)
        raise TimeoutError(
            f"No response to {command!r} on {self.port}; "
            f"check firmware, port ownership and USB CDC. Other output: {recently_seen}"
        )

    def _exchange(self, command: str, expected: str, timeout: float = 3.0) -> str:
        received = self._read_response(command, timeout=timeout)
        if received != expected:
            raise RuntimeError(
                f"Unexpected response to {command!r}: expected {expected!r}, "
                f"received {received!r}"
            )
        return received

    def ping(self) -> bool:
        self._exchange("PING", "PONG")
        return True

    def info(self) -> str:
        if self._firmware_info is not None:
            return self._firmware_info
        response = self._read_response("INFO")
        if not response.startswith("INFO "):
            raise RuntimeError(f"Unexpected INFO response: {response}")
        self._firmware_info = response[5:]
        return self._firmware_info

    def set_face(self, face: str) -> None:
        if not isinstance(face, str) or face.lower() not in self.VALID_FACES:
            raise ValueError(f"Unknown face {face!r}. Choices: {sorted(self.VALID_FACES)}")
        mode = face.upper()
        self._exchange(f"FACE {mode}", f"OK FACE {mode}")

    def _expect_line(self, expected: str, *, timeout: float) -> str:
        """Wait for one response, skipping unrelated startup/debug lines."""
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("StackChan not connected")
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            raw = self._serial.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line:
                continue
            if line == expected:
                return line
            if line.startswith("ERR "):
                raise RuntimeError(f"StackChan: {line}")
            if len(seen) < 5:
                seen.append(line)
        raise TimeoutError(
            f"Waiting for {expected!r} timed out on {self.port}. "
            f"Other lines: {seen}"
        )

    def set_volume(self, level: int) -> None:
        """Set moderate output volume. v0.4 firmware only."""
        if not isinstance(level, int) or not 0 <= level <= 160:
            raise ValueError("Volume must be an integer from 0 to 160")
        self._exchange(f"VOLUME {level}", f"OK VOLUME {level}")

    def play_pcm(self, pcm: bytes, *, sample_rate: int = 16000) -> None:
        """Upload and play <=12s PCM16LE mono with negotiated flow control.

        Only send 1024-byte packets when the firmware explicitly advertises
        AUDIO4B support and its enlarged receive buffer. Older devices retain
        their 128-byte handshake. Never infer a safe packet size from baudrate.
        """
        self.last_audio_timing = {}
        if sample_rate != 16000:
            raise ValueError("Expected 16000Hz PCM16LE mono audio")
        if not isinstance(pcm, bytes):
            raise TypeError("pcm must be bytes")
        if not 2 <= len(pcm) <= 384000 or (len(pcm) & 1):
            raise ValueError("PCM16 audio must be 0..12 seconds and even-sized")
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("StackChan not connected")
        info = self.info()
        if "V4_AUDIO_PCM16_16K_ACK128" not in info:
            raise RuntimeError(
                "Firmware lacks the ACK128 audio capability; reported " + repr(info)
            )

        requested_codec = os.getenv("FRISTON_STACKCHAN_AUDIO_CODEC", "adpcm").strip().lower()
        if requested_codec not in ("adpcm", "pcm"):
            raise ValueError("FRISTON_STACKCHAN_AUDIO_CODEC must be 'adpcm' or 'pcm'")
        use_adpcm = requested_codec == "adpcm" and "ADPCM4" in info
        packet_bytes = 128
        if use_adpcm:
            wire = _encode_ima_adpcm(pcm)
            command = "AUDIO4"
            if "ADPCM4_ACK1024" in info:
                command = "AUDIO4B"
                packet_bytes = 1024
            header = f"{command} {len(pcm)//2} {zlib.crc32(wire) & 0xFFFFFFFF:08X}\n"
            codec_name = "ADPCM4"
        else:
            wire = pcm
            header = f"AUDIO {len(pcm)} {zlib.crc32(wire) & 0xFFFFFFFF:08X}\n"
            codec_name = "PCM16"

        started_upload = time.perf_counter()
        self._serial.write(header.encode("ascii"))
        self._serial.flush()
        self._expect_line("READY AUDIO", timeout=3.0)

        # One acknowledged packet in flight; CRC prevents corrupted playback.
        for offset in range(0, len(wire), packet_bytes):
            chunk = wire[offset : offset + packet_bytes]
            sent = self._serial.write(chunk)
            if sent != len(chunk):
                raise RuntimeError(
                    f"Partial USB write: {sent}/{len(chunk)} at offset {offset}"
                )
            self._serial.flush()
            confirmed = offset + len(chunk)
            try:
                self._expect_line(f"ACK {confirmed}", timeout=5.0)
            except (RuntimeError, TimeoutError) as exc:
                raise RuntimeError(
                    f"{codec_name} transfer stopped at {confirmed}/{len(wire)} "
                    f"bytes (last confirmed {offset}): {exc}"
                ) from exc

        clip_duration = len(pcm) / 32000.0
        self._expect_line("AUDIO STARTED", timeout=10.0)
        upload_seconds = time.perf_counter() - started_upload
        started_playback = time.perf_counter()
        self._expect_line("AUDIO DONE", timeout=clip_duration + 7.0)
        playback_seconds = time.perf_counter() - started_playback
        self.last_audio_timing = {
            "codec": codec_name, "transfer_s": upload_seconds,
            "started_at": started_playback, "playback_s": playback_seconds,
            "audio_s": clip_duration,
        }
        if os.getenv("FRISTON_STACKCHAN_AUDIO_DIAG", "1").lower() not in (
                "0", "no", "false", "off"):
            print(
                f"[StackChanUSB] codec={codec_name} ack={packet_bytes} wire={len(wire)}B "
                f"raw={len(pcm)}B transfer={upload_seconds:.2f}s "
                f"playback={playback_seconds:.2f}s "
                f"audio={clip_duration:.2f}s",
                flush=True,
            )

    def close(self) -> None:
        self._firmware_info = None
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def __enter__(self) -> "StackChanBridge":
        return self.connect()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Friston-3 StackChan face controller")
    parser.add_argument("--port", default=os.getenv("FRISTON_STACKCHAN_PORT", "COM10"))
    parser.add_argument("--face", choices=sorted(StackChanBridge.VALID_FACES))
    parser.add_argument(
        "--interactive", action="store_true",
        help="Keep COM port open and switch faces interactively (no re-flash)",
    )
    parser.add_argument(
        "--hold", type=float, default=0,
        help="Keep COM port open for N seconds after sending a face",
    )
    args = parser.parse_args()
    if not 0 <= args.hold <= 300:
        parser.error("--hold must be between 0 and 300 seconds")

    try:
        with StackChanBridge(port=args.port) as robot:
            print(f"Connected: {args.port} ({robot.info()})")
            if args.face:
                robot.set_face(args.face)
                print(f"Face switched to {args.face}")
            else:
                print("PING -> PONG")

            if args.interactive:
                print("Keep-open mode. Type a face name, or 'quit' to exit.")
                print("Available:", ", ".join(sorted(StackChanBridge.VALID_FACES)))
                while True:
                    try:
                        face = input("face> ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        break
                    if face in ("exit", "quit", "q"):
                        break
                    if not face:
                        continue
                    if face not in StackChanBridge.VALID_FACES:
                        print(f"Unknown face: {face!r}")
                        continue
                    robot.set_face(face)
                    print(f"OK FACE {face.upper()} (COM kept open)")
            elif args.hold:
                print(f"Keeping {args.port} open for {args.hold:g} seconds...")
                time.sleep(args.hold)
    except (serial.SerialException, OSError, TimeoutError, RuntimeError) as exc:
        parser.exit(1, f"StackChan connection failed: {exc}\n")


if __name__ == "__main__":
    main()
