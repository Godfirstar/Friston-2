"""Single-owner COM10 scheduler for Friston-3 expressions + audio.

Uses capability negotiation in stackchan_bridge.py for old and new firmware.
The firmware controls mouth motion automatically during audio playback.
No automatic tests; operations are driven only by the main application.
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
class _AudioJob:
    pcm: bytes
    volume: int
    done: threading.Event = field(default_factory=threading.Event)
    error: Optional[Exception] = None


class StackChanFaceEvents:
    """Own one serial connection across the program's lifetime.

    Expressions are fire-and-forget; audio transfer is synchronous so the
    next response is not accepted until the robot finishes speaking.
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
        self._audio_jobs: deque[_AudioJob] = deque()
        self._worker: Optional[threading.Thread] = None
        self.last_audio_timing: dict[str, float | str] = {}
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

    def play_pcm(self, pcm: bytes, *, volume: int = 60) -> bool:
        """Play through the existing COM owner, waiting for AUDIO DONE.

        Used by AgentSpeechOutput after the separate TTS environment finishes
        creating a short segment of 16kHz mono signed-16-bit little-endian PCM.
        """
        if not self.enabled:
            return False
        if not isinstance(pcm, bytes) or not pcm or len(pcm) > 384000:
            raise ValueError("PCM must be 1..384000 bytes of 16kHz PCM16 mono")
        if len(pcm) % 2:
            raise ValueError("PCM16 requires an even number of bytes")
        if not isinstance(volume, int) or not 0 <= volume <= 160:
            raise ValueError("Volume must be 0..160")
        job = _AudioJob(pcm=pcm, volume=volume)
        self.last_audio_timing = {}
        with self._cond:
            if self._closed:
                raise RuntimeError("StackChan device manager is closed")
            self._audio_jobs.append(job)
            self._cond.notify()
        # Each 128-byte ACK requires a round trip; allow time for slower PCs.
        duration = len(pcm) / 32000.0
        timeout = max(30.0, min(180.0, duration * 16.0 + 20.0))
        if not job.done.wait(timeout=timeout):
            raise TimeoutError(
                f"StackChan did not finish PCM transfer within {timeout:.0f}s. "
                "Do not start a second COM10 client while the old job is active."
            )
        if job.error:
            raise RuntimeError("StackChan audio failed") from job.error
        return True

    def _run(self) -> None:
        bridge = StackChanBridge(port=self.port)
        delivered_revision = 0
        next_connect_time = 0.0
        last_warning = 0.0
        try:
            while True:
                with self._cond:
                    while (not self._closed and
                           self._version == delivered_revision and
                           not self._audio_jobs):
                        self._cond.wait()
                    if self._closed:
                        break
                    delay = next_connect_time - time.monotonic()
                    if delay > 0:
                        self._cond.wait(timeout=delay)
                        continue
                    # Face commands first, then queued audio, on the same port.
                    if self._version != delivered_revision:
                        face = self._wanted
                        revision = self._version
                        job = None
                    else:
                        face = None
                        revision = delivered_revision
                        job = self._audio_jobs.popleft()

                try:
                    bridge.connect()
                    if job is None:
                        bridge.set_face(face)
                        delivered_revision = revision
                    else:
                        bridge.set_volume(job.volume)
                        bridge.play_pcm(job.pcm)
                        self.last_audio_timing = dict(bridge.last_audio_timing)
                        job.done.set()
                    next_connect_time = 0.0
                except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                    bridge.close()
                    next_connect_time = time.monotonic() + 3.0
                    if job is not None:
                        job.error = exc
                        job.done.set()
                    if time.monotonic() - last_warning >= 15.0 or not last_warning:
                        LOG.warning("StackChan unavailable (%s): %s", self.port, exc)
                        last_warning = time.monotonic()
                    # Failed face events retry automatically. Failed audio
                    # commands report errors to caller rather than replaying.
                    continue
        finally:
            with self._cond:
                while self._audio_jobs:
                    job = self._audio_jobs.popleft()
                    job.error = RuntimeError("Device manager closed before playback")
                    job.done.set()
            # Never create a connection merely to reset avatar on shutdown.
            if bridge._serial is not None:
                try:
                    bridge.set_face("idle")
                except Exception:
                    pass
                bridge.close()

    def close(self) -> None:
        if not self.enabled:
            return
        with self._cond:
            self._closed = True
            self._cond.notify()
        if self._worker is not None:
            self._worker.join(timeout=2.0)

    def __enter__(self) -> "StackChanFaceEvents":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
