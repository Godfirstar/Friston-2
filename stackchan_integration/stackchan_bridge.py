"""Friston-3 <-> StackChan K151 USB bridge.

Negotiate ADPCM4/ACK1024 for fast uploads; retain ACK128/PCM16 compatibility.
Every clip is CRC-checked by the device before playback. Sensor downloads
use bounded, CRC-checked DATA frames on this same connection. This module
never starts a second serial reader; call it through StackChanFaceEvents.
Only one process may own COM10.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
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
        self.last_camera_timing: dict[str, float | int] = {}
        self.last_mic_timing: dict[str, float | int] = {}
        self._cancelled: Optional[threading.Event] = None
        self._deadline: Optional[float] = None

    def _set_operation(self, cancelled=None, deadline=None) -> None:
        """The serial owner's cancellation context; never call from another reader."""
        self._cancelled = cancelled
        self._deadline = deadline

    def _check_active(self) -> None:
        if self._cancelled is not None and self._cancelled.is_set():
            raise RuntimeError("StackChan operation cancelled")
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise TimeoutError("StackChan operation deadline exceeded")

    def _write_bytes(self, data: bytes) -> None:
        self._check_active()
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("StackChan not connected")
        sent = self._serial.write(data)
        if sent != len(data):
            raise RuntimeError(f"Partial USB write: {sent}/{len(data)}")
        # No flush() here: it can block indefinitely on a disconnected Windows
        # COM device. Protocol acknowledgements confirm that data was received.

    def _send_line(self, command: str) -> None:
        if (not command.isascii() or len(command) > 160
                or any(ch in command for ch in "\r\n")):
            raise ValueError("Commands must be one short ASCII line")
        self._write_bytes((command + "\n").encode("ascii"))

    def _read_line(self, deadline: float) -> str:
        """Read a whole bounded line even if a USB read times out halfway."""
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("StackChan not connected")
        line = bytearray()
        while time.monotonic() < deadline:
            self._check_active()
            char = self._serial.read(1)
            if not char:
                continue
            if char == b"\n":
                return bytes(line).decode("ascii", errors="replace").strip()
            line.extend(char)
            if len(line) > 256:
                raise RuntimeError("StackChan protocol line exceeds 256 bytes")
        raise TimeoutError(f"StackChan response timed out on {self.port}")

    def connect(self) -> "StackChanBridge":
        self._check_active()
        if self._serial is not None and self._serial.is_open:
            return self
        try:
            self._serial = serial.Serial(
                self.port, self.baudrate, timeout=0.15, write_timeout=1.0
            )
            # Some ESP32 USB CDC configurations reboot when the port opens.
            # The nominal baudrate is USB CDC line coding, not a UART bitrate.
            settle_until = time.monotonic() + 1.2
            while time.monotonic() < settle_until:
                self._check_active()
                time.sleep(0.05)
            self._serial.reset_input_buffer()
            self._exchange("PING", "PONG", timeout=5.0)
            return self
        except Exception:
            self.close()
            raise

    def _read_response(self, command: str, timeout: float = 3.0) -> str:
        self._send_line(command)
        deadline = time.monotonic() + timeout
        recently_seen = []
        while time.monotonic() < deadline:
            line = self._read_line(deadline)
            if not line:
                continue
            if line.startswith("ERR "):
                raise RuntimeError(f"StackChan rejected {command!r}: {line}")
            if (line == "PONG" or line.startswith("OK FACE ")
                    or line.startswith("OK VOLUME ") or line.startswith("INFO ")
                    or line == "OK CAMERA CLOSE"):
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
        self._check_active()
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
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            line = self._read_line(deadline)
            if not line:
                continue
            if line == expected:
                return line
            if line.startswith("ERR "):
                raise RuntimeError(f"StackChan: {line}")
            if len(seen) < 5:
                seen.append(line)
        raise TimeoutError(
            f"Waiting for {expected!r} timed out on {self.port}. Other lines: {seen}"
        )

    def _require_capability(self, capability: str) -> None:
        if capability not in self.info():
            raise RuntimeError(
                f"栈酱固件缺少 {capability}；请先烧录本补丁中的摄像头/麦克风固件。"
            )

    def _receive_blob(self, command: str, *, kind: str, dimensions: tuple[int, int],
                      max_bytes: int, header_timeout: float) -> tuple[bytes, dict]:
        """Download one sensor result; no line reads are allowed inside its bytes."""
        started = time.perf_counter()
        sent_command = False
        try:
            self._send_line(command)
            sent_command = True
            header_deadline = time.monotonic() + header_timeout
            while True:
                line = self._read_line(header_deadline)
                if line.startswith("ERR ") or line == "MIC CANCELLED":
                    raise RuntimeError(f"StackChan: {line}")
                if line == "MIC EMPTY" and kind == "PCM16":
                    return b"", {"capture_s": time.perf_counter() - started,
                                  "transfer_s": 0.0, "bytes": 0}
                if line == "MIC STARTED" and kind == "PCM16":
                    print("[StackChanMIC] 正在录音，说完后稍作停顿。", flush=True)
                    continue
                if line.startswith("DATA "):
                    break
                # MIC STARTED and short startup/status lines are not payload.

            fields = line.split()
            if len(fields) != 6 or fields[:2] != ["DATA", kind]:
                raise RuntimeError(f"Invalid StackChan DATA header: {line!r}")
            count = int(fields[2])
            if (len(fields[3]) != 8
                    or any(c not in "0123456789abcdefABCDEF" for c in fields[3])):
                raise RuntimeError("Invalid DATA CRC32")
            expected_crc = int(fields[3], 16)
            actual_dimensions = (int(fields[4]), int(fields[5]))
            if actual_dimensions != dimensions or not 1 <= count <= max_bytes:
                raise RuntimeError(f"Unexpected DATA dimensions/size: {line!r}")
            if kind == "PCM16" and count % 2:
                raise RuntimeError("Microphone PCM16 byte count must be even")

            header_ready = time.perf_counter()
            self._send_line("READ DATA")
            # USB CDC normally transfers much faster. These are failure bounds,
            # not assumed frame rates; do not derive them from 115200 line coding.
            transfer_deadline = time.monotonic() + max(5.0, count / 16000.0 + 3.0)
            received = bytearray()
            crc = 0
            while len(received) < count:
                self._check_active()
                chunk_size = min(1024, count - len(received))
                chunk = bytearray()
                chunk_deadline = min(transfer_deadline, time.monotonic() + 3.0)
                while len(chunk) < chunk_size:
                    self._check_active()
                    if time.monotonic() >= chunk_deadline:
                        raise TimeoutError(
                            f"Sensor transfer stopped at {len(received)+len(chunk)}/{count} bytes"
                        )
                    part = self._serial.read(chunk_size - len(chunk))
                    if part:
                        chunk.extend(part)
                received.extend(chunk)
                crc = zlib.crc32(chunk, crc)
                self._send_line(f"ACK {len(received)}")
            self._expect_line("DONE DATA", timeout=3.0)
            if (crc & 0xFFFFFFFF) != expected_crc:
                raise RuntimeError("StackChan sensor CRC32 mismatch; discarded frame/recording")
            timing = {"capture_s": header_ready - started,
                      "transfer_s": time.perf_counter() - header_ready,
                      "bytes": count}
            return bytes(received), timing
        except Exception:
            # At all sensor protocol waits CANCEL is a text command. Sending it
            # releases a firmware MIC wait or DATA/ACK wait before reconnecting.
            # Never do this inside the legacy host-to-device AUDIO binary path.
            if sent_command and self._serial is not None:
                try:
                    self._serial.write(b"CANCEL\n")
                except Exception:
                    pass
            self.close()
            raise

    def capture_jpeg(self, width: int = 320) -> bytes:
        if type(width) is not int or width not in (320, 640):
            raise ValueError("Camera width must be 320 or 640")
        self._require_capability("CAMERA_JPEG_V1")
        self.last_camera_timing = {}
        jpeg, timing = self._receive_blob(
            f"CAMERA {width}", kind="JPEG", dimensions=(width, width * 3 // 4),
            max_bytes=640 * 480 * 2, header_timeout=8.0,
        )
        self.last_camera_timing = timing
        return jpeg

    def close_camera(self) -> None:
        self._require_capability("CAMERA_JPEG_V1")
        self._exchange("CAMERA CLOSE", "OK CAMERA CLOSE", timeout=5.0)

    def record_pcm(self, mode: str = "touch", *, wait_ms: int = 1000,
                   max_ms: int = 8000, silence_ms: int = 700,
                   threshold: int = 350) -> bytes:
        mode = str(mode).upper()
        if mode not in ("TOUCH", "VAD"):
            raise ValueError("Microphone mode must be 'touch' or 'vad'")
        for name, value, low, high in (
            ("wait_ms", wait_ms, 100, 10000), ("max_ms", max_ms, 1000, 12000),
            ("silence_ms", silence_ms, 200, 2000), ("threshold", threshold, 30, 10000),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in {low}..{high}")
        self._require_capability("MIC_PCM16_V1")
        self.last_mic_timing = {}
        pcm, timing = self._receive_blob(
            f"MIC {mode} {wait_ms} {max_ms} {silence_ms} {threshold}",
            kind="PCM16", dimensions=(16000, 1), max_bytes=384000,
            header_timeout=(wait_ms + max_ms) / 1000.0 + 4.0,
        )
        self.last_mic_timing = timing
        return pcm

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
        self._write_bytes(header.encode("ascii"))
        self._expect_line("READY AUDIO", timeout=3.0)

        # One acknowledged packet in flight; CRC prevents corrupted playback.
        for offset in range(0, len(wire), packet_bytes):
            chunk = wire[offset : offset + packet_bytes]
            self._write_bytes(chunk)
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
