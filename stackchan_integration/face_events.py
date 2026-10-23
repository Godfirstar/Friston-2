"""Single-owner USB scheduler for Friston-3 expressions, audio and sensors.

Only this worker opens COM10. Camera, microphone and speaker transactions are
serialized, so binary image/audio bytes cannot be mistaken for text responses.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from .stackchan_bridge import StackChanBridge

LOG = logging.getLogger(__name__)


@dataclass
class _DeviceJob:
    operation: str
    arguments: dict
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: Optional[Exception] = None


class StackChanFaceEvents:
    """Keep one serial owner for the lifetime of the application.

    Expressions are coalesced; all sensor/audio requests have a bounded queue
    lifetime. An expired request cannot unexpectedly run later. Cancellation
    is handled by the owner, never by a second thread closing/reading its port.
    """

    def __init__(self, port: Optional[str] = None, enabled: Optional[bool] = None):
        self.port = port or os.getenv("FRISTON_STACKCHAN_PORT", "COM10")
        self.enabled = (
            os.getenv("FRISTON_STACKCHAN_ENABLED", "1").strip().lower()
            not in ("0", "false", "off", "no")
            if enabled is None else enabled
        )
        self._cond = threading.Condition()
        self._wanted = "idle"
        self._version = 1
        self._closed = False
        self._jobs: deque[_DeviceJob] = deque()
        self._active_job: Optional[_DeviceJob] = None
        self._stop_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self.last_audio_timing: dict[str, float | str] = {}
        self.last_camera_timing: dict[str, float | int] = {}
        self.last_mic_timing: dict[str, float | int] = {}
        if self.enabled:
            self._worker = threading.Thread(
                target=self._run, daemon=True, name="stackchan-hardware"
            )
            self._worker.start()

    def set_face(self, face: str) -> None:
        face = face.lower()
        if face not in StackChanBridge.VALID_FACES:
            raise ValueError(f"Invalid StackChan expression: {face}")
        if not self.enabled:
            return
        with self._cond:
            if self._closed:
                return
            self._wanted = face
            self._version += 1
            self._cond.notify()

    def on_request(self) -> None:
        self.set_face("thinking")

    def on_reply_ready(self) -> None:
        self.set_face("idle")

    def on_speech_start(self) -> None:
        self.set_face("speaking")

    def on_speech_end(self) -> None:
        self.set_face("idle")

    def on_error(self) -> None:
        self.set_face("sad")

    def _submit(self, operation: str, *, timeout: float, **arguments):
        if not self.enabled:
            raise RuntimeError("栈酱已禁用；使用其摄像头/麦克风需启用 FRISTON_STACKCHAN_ENABLED")
        job = _DeviceJob(operation, arguments, time.monotonic() + timeout)
        with self._cond:
            if self._closed:
                raise RuntimeError("StackChan device manager is closed")
            if len(self._jobs) >= 8:
                raise RuntimeError("StackChan request queue is full")
            self._jobs.append(job)
            self._cond.notify()
        try:
            completed = False
            # Short waits let Windows Python dispatch Ctrl+C while a hardware
            # transaction is pending; use the original deadline for expiry.
            while not completed:
                remaining = job.deadline - time.monotonic()
                if remaining <= 0:
                    break
                completed = job.done.wait(timeout=min(0.1, remaining))
        except BaseException:
            # In particular Ctrl+C: never leave a queued recording or playback
            # request that can start after the caller has abandoned it.
            self._cancel(job)
            raise
        if not completed:
            with self._cond:
                # Avoid discarding a result completed at the timeout boundary.
                completed = job.done.is_set()
            if not completed:
                self._cancel(job)
                raise TimeoutError(
                    f"StackChan {operation} exceeded {timeout:.0f}s; request cancelled."
                )
        if job.error is not None:
            raise RuntimeError(f"StackChan {operation} failed: {job.error}") from job.error
        return job.result

    def _cancel(self, job: _DeviceJob) -> None:
        with self._cond:
            job.cancelled.set()
            try:
                self._jobs.remove(job)
            except ValueError:
                # The serial owner will observe cancellation during active I/O.
                pass
            else:
                job.error = RuntimeError("StackChan request cancelled before execution")
                job.done.set()
            self._cond.notify_all()

    def get_info(self) -> str:
        return self._submit("info", timeout=12.0)

    def move_head(self, x_tenths: int, y_tenths: int, speed: int = 300) -> None:
        if type(x_tenths) is not int or not -900 <= x_tenths <= 900:
            raise ValueError("x_tenths must be an integer in -900..900")
        if type(y_tenths) is not int or not 50 <= y_tenths <= 850:
            raise ValueError("y_tenths must be an integer in 50..850")
        if type(speed) is not int or not 100 <= speed <= 800:
            raise ValueError("speed must be an integer in 100..800")
        self._submit("move_head", timeout=8.0, x_tenths=x_tenths,
                     y_tenths=y_tenths, speed=speed)

    def home_head(self) -> None:
        self._submit("home_head", timeout=8.0)

    def stop_head(self) -> None:
        self._submit("stop_head", timeout=8.0)

    def capture_jpeg(self, width: int = 320) -> bytes:
        if type(width) is not int or width not in (320, 640):
            raise ValueError("Camera width must be 320 or 640")
        self.last_camera_timing = {}
        return self._submit("camera", timeout=55.0, width=width)

    def capture_identity_jpeg(self, width: int = 320, *, timeout: float = 2.5) -> bytes:
        """One bounded capture-and-release job, shared with MIC/TTS/photo I/O."""
        if type(width) is not int or width not in (320, 640):
            raise ValueError("Camera width must be 320 or 640")
        if not isinstance(timeout, (int, float)) or not 0.5 <= timeout <= 8:
            raise ValueError("Identity camera timeout must be 0.5..8 seconds")
        return self._submit("identity_camera", timeout=float(timeout), width=width)

    def close_camera(self) -> None:
        self._submit("camera_close", timeout=12.0)

    def record_pcm(self, mode: str = "touch", *, wait_ms: int = 1000,
                   max_ms: int = 8000, silence_ms: int = 700,
                   threshold: int = 350) -> bytes:
        # Reject invalid limits before they can turn into an unbounded wait.
        mode = str(mode).lower()
        if mode not in ("touch", "vad"):
            raise ValueError("Microphone mode must be 'touch' or 'vad'")
        for name, value, low, high in (
            ("wait_ms", wait_ms, 100, 10000), ("max_ms", max_ms, 1000, 12000),
            ("silence_ms", silence_ms, 200, 2000), ("threshold", threshold, 30, 10000),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in {low}..{high}")
        self.last_mic_timing = {}
        return self._submit(
            "microphone", timeout=(wait_ms + max_ms) / 1000.0 + 40.0,
            mode=mode, wait_ms=wait_ms, max_ms=max_ms,
            silence_ms=silence_ms, threshold=threshold,
        )

    def play_pcm(self, pcm: bytes, *, volume: int = 60) -> bool:
        """Upload and play through the same COM owner; wait for AUDIO DONE."""
        if not self.enabled:
            return False
        if not isinstance(pcm, bytes) or not pcm or len(pcm) > 384000 or len(pcm) % 2:
            raise ValueError("PCM must be even-sized, 2..384000 bytes, PCM16LE/16kHz mono")
        if type(volume) is not int or not 0 <= volume <= 160:
            raise ValueError("Volume must be 0..160")
        self.last_audio_timing = {}
        duration = len(pcm) / 32000.0
        timeout = max(30.0, min(180.0, duration * 16.0 + 20.0))
        self._submit("audio", timeout=timeout, pcm=pcm, volume=volume)
        return True

    # MOTION_LINK_V1: gestures share the existing serial transaction worker.
    def on_task_completed(self, result) -> None:
        from .motion_events import on_task_completed
        on_task_completed(self, result)

    def _execute(self, bridge: StackChanBridge, job: _DeviceJob):
        if job.operation == "success_nod":
            from .motion_events import execute_success_nod
            return execute_success_nod(
                bridge, job,
                camera_active=getattr(self, "_motion_camera_active", False))
        # Keep camera ownership conservative after failures. v3 MOVE can close
        # the camera, so automatic gestures must never run during its session.
        if job.operation in ("camera", "identity_camera"):
            self._motion_camera_active = True
        result = self._execute_core(bridge, job)
        if job.operation in ("camera_close", "identity_camera", "audio", "microphone"):
            self._motion_camera_active = False
        return result

    def _execute_core(self, bridge: StackChanBridge, job: _DeviceJob):
        if job.operation == "info":
            return bridge.info()
        if job.operation == "move_head":
            return bridge.move_head(**job.arguments)
        if job.operation == "home_head":
            return bridge.home_head()
        if job.operation == "stop_head":
            return bridge.stop_head()
        if job.operation == "camera":
            result = bridge.capture_jpeg(**job.arguments)
            self.last_camera_timing = dict(bridge.last_camera_timing)
            return result
        if job.operation == "identity_camera":
            try:
                result = bridge.capture_jpeg(**job.arguments)
                self.last_camera_timing = dict(bridge.last_camera_timing)
                bridge.close_camera()
                return result
            except Exception:
                # JPEG may already be complete when close_camera hits the job
                # deadline. Its transfer cleanup no longer runs at that point.
                # Only this existing COM owner sends CANCEL; firmware releases
                # camera/I2C without adding another connection or waiting loop.
                if bridge._serial is not None:
                    try:
                        bridge._serial.write(b"CANCEL\n")
                    except Exception:
                        pass
                raise
        if job.operation == "camera_close":
            return bridge.close_camera()
        if job.operation == "microphone":
            result = bridge.record_pcm(**job.arguments)
            self.last_mic_timing = dict(bridge.last_mic_timing)
            return result
        if job.operation == "audio":
            bridge.set_volume(job.arguments["volume"])
            bridge.play_pcm(job.arguments["pcm"])
            self.last_audio_timing = dict(bridge.last_audio_timing)
            return True
        raise ValueError(f"Unknown StackChan operation: {job.operation}")

    def _run(self) -> None:
        bridge = StackChanBridge(port=self.port)
        delivered_revision = 0
        next_connect_time = 0.0
        last_warning = 0.0
        try:
            while True:
                with self._cond:
                    now = time.monotonic()
                    while self._jobs and (
                        self._jobs[0].cancelled.is_set() or self._jobs[0].deadline <= now
                    ):
                        expired = self._jobs.popleft()
                        expired.error = TimeoutError("StackChan request expired before execution")
                        expired.done.set()
                    if self._closed:
                        break
                    if not self._jobs and self._version == delivered_revision:
                        self._cond.wait()
                        continue
                    delay = next_connect_time - now
                    if delay > 0:
                        self._cond.wait(timeout=delay)
                        continue
                    # Claim the job BEFORE attempting a face update. If the COM
                    # connection fails, report it to the waiting caller rather
                    # than retrying a face forever and starving sensor requests.
                    job = self._jobs.popleft() if self._jobs else None
                    self._active_job = job
                    revision = self._version
                    face = self._wanted if revision != delivered_revision else None

                bridge._set_operation(
                    job.cancelled if job is not None else self._stop_event,
                    job.deadline if job is not None else time.monotonic() + 10.0,
                )
                try:
                    bridge.connect()
                    if face is not None:
                        bridge.set_face(face)
                        delivered_revision = revision
                    if job is not None:
                        job.result = self._execute(bridge, job)
                    next_connect_time = 0.0
                except Exception as exc:
                    bridge.close()
                    next_connect_time = time.monotonic() + 3.0
                    if job is not None:
                        job.error = exc
                        # Legacy AUDIO receive/playback has no CANCEL command.
                        # ANY failed upload may leave it consuming raw bytes for
                        # its 10s receive timeout. Early PINGs would be mistaken
                        # for audio and refresh that timeout indefinitely.
                        if job.operation == "audio":
                            next_connect_time = time.monotonic() + 15.0
                    if not self._stop_event.is_set() and (
                        not last_warning or time.monotonic() - last_warning >= 15.0
                    ):
                        LOG.warning("StackChan unavailable (%s): %s", self.port, exc)
                        last_warning = time.monotonic()
                finally:
                    bridge._set_operation()
                    with self._cond:
                        self._active_job = None
                        if job is not None:
                            job.done.set()
                        self._cond.notify_all()
        finally:
            with self._cond:
                while self._jobs:
                    job = self._jobs.popleft()
                    job.error = RuntimeError("Device manager closed before execution")
                    job.done.set()
            # Only the owner closes its port; never open a new connection merely
            # to reset the avatar during shutdown.
            bridge.close()

    def close(self) -> None:
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._stop_event.set()
            if self._active_job is not None:
                self._active_job.cancelled.set()
            while self._jobs:
                job = self._jobs.popleft()
                job.cancelled.set()
                job.error = RuntimeError("Device manager closed before execution")
                job.done.set()
            self._cond.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=2.0)

    def __enter__(self) -> "StackChanFaceEvents":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
