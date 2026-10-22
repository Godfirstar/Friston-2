"""Persistent offline synthesis and bounded audio batching for StackChan.

Keeps the isolated Sherpa-ONNX TTS process alive and *coalesces adjacent PCM
segments* up to 12 seconds before sending one audio command. This eliminates
large serial/pre-buffering gaps in typical multi-sentence replies.
Works with the existing single-COM-owner face_events.py and either v0.4.1 or
v0.5.2 bridge/firmware. No automatic test files or scripts are included.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from .face_events import StackChanFaceEvents


class AgentSpeechOutput:
    """Voice output with asynchronous initialization and sequential playback."""

    def __init__(self, face: StackChanFaceEvents):
        self.face = face
        self.enabled = os.getenv("FRISTON_TTS_AUTO", "1").lower() not in (
            "0", "off", "false", "no"
        )
        try:
            self.volume = int(os.getenv("FRISTON_TTS_VOLUME", "60"))
        except ValueError:
            self.volume = 60
        self.volume = max(0, min(160, self.volume))

        self._proc: Optional[subprocess.Popen] = None
        self._messages: queue.Queue = queue.Queue()
        self._speech_lock = threading.Lock()
        self._sequence = 0
        self._closed = False
        self.last_speech_timing: dict[str, float] = {}
        atexit.register(self.close)
        if self.enabled and self.face.enabled:
            try:
                self._launch_worker()
                print("[StackChanTTS] 后台预热离线语音引擎（只加载一次模型）。")
            except (OSError, ValueError, RuntimeError) as exc:
                print(f"[StackChanTTS] 后台引擎未启动，仍可正常文字聊天：{exc}")

    @staticmethod
    def _tts_python() -> Path:
        explicit = os.getenv("FRISTON_TTS_PYTHON", "").strip().strip('"')
        if explicit:
            candidate = Path(explicit)
        else:
            candidate = Path(sys.executable).parent.parent / "friston_tts" / "python.exe"
        if not candidate.is_file():
            raise FileNotFoundError(
                f"找不到 friston_tts Python: {candidate}；"
                "可通过 FRISTON_TTS_PYTHON 指定。"
            )
        return candidate

    def _launch_worker(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        python_exe = self._tts_python()
        self._messages = queue.Queue()
        self._proc = subprocess.Popen(
            [str(python_exe), "-B", "-u", "-m", "stackchan_integration.tts_daemon"],
            cwd=str(Path(__file__).resolve().parent.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        proc = self._proc
        messages = self._messages

        def reader() -> None:
            try:
                if proc.stdout is None:
                    raise RuntimeError("Worker stdout is not available")
                while True:
                    raw = proc.stdout.readline()
                    if not raw:
                        break
                    try:
                        obj = json.loads(raw.decode("utf-8", errors="strict"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        # Native libraries may print diagnostics. Do not confuse
                        # those with protocol events, or block the producer pipe.
                        continue
                    if isinstance(obj, dict) and "event" in obj:
                        messages.put(obj)
            except Exception as exc:
                messages.put({"event": "fatal", "error": str(exc)})
            finally:
                messages.put({"event": "worker_exited", "code": proc.poll()})

        threading.Thread(
            target=reader, daemon=True, name="friston-tts-output"
        ).start()

    def speak(self, reply: str) -> bool:
        """Speak NPC text, batching adjacent clips into <=12s PCM commands.

        The TTS daemon synthesizes chunks in a persistent subprocess and emits
        PCM file events. We wait at most a small configurable prefetch window
        after the first chunk to join adjacent clips. The whole joined buffer
        plays as ONE speaker command, removing long mid-sentence gaps.
        """
        if not self.enabled or not self.face.enabled or self._closed:
            return False
        reply = str(reply).strip()
        if not reply:
            return False
        if len(reply) > 500:
            print("[StackChanTTS] 回复超过 500 字，保留文字，不播音。")
            return False

        with self._speech_lock:
            self.last_speech_timing = {}
            try:
                self._launch_worker()
                assert self._proc is not None and self._proc.stdin is not None
                self._sequence += 1
                request_id = self._sequence
                with tempfile.TemporaryDirectory(prefix="friston3_tts_") as output_dir:
                    msg = {
                        "cmd": "speak", "id": request_id,
                        "text": reply, "output_dir": output_dir,
                    }
                    start = time.perf_counter()
                    self._proc.stdin.write(
                        (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
                    )
                    self._proc.stdin.flush()

                    max_pcm_bytes = 384000  # 12 seconds, mono PCM16LE/16kHz
                    try:
                        prefetch = float(os.getenv("FRISTON_TTS_PACK_WAIT_S", "0.25"))
                    except ValueError:
                        prefetch = 0.25
                    prefetch = min(5.0, max(0.0, prefetch))
                    segments_seen = 0
                    clips_played = 0
                    audio_secs = 0.0
                    total_hw_secs = 0.0
                    waiting_pcm: list[bytes] = []
                    waiting_bytes = 0
                    waiting_segments = 0
                    first_ready_at: Optional[float] = None
                    synth_done = False
                    expected_segments = -1
                    first_audio_s: Optional[float] = None

                    def flush_audio() -> None:
                        nonlocal waiting_pcm, waiting_bytes, waiting_segments
                        nonlocal first_ready_at, clips_played, audio_secs, total_hw_secs
                        nonlocal first_audio_s
                        if not waiting_pcm:
                            return
                        combined = b"".join(waiting_pcm)
                        seconds = len(combined) / 32000.0
                        print(
                            f"[StackChanTTS] 合并 {waiting_segments} 段 → "
                            f"播放第 {clips_played + 1} 条音频（{seconds:.2f}s）；"
                            "正在传输播放..."
                        )
                        began = time.perf_counter()
                        self.face.play_pcm(combined, volume=self.volume)
                        elapsed = time.perf_counter() - began
                        if first_audio_s is None:
                            started_at = self.face.last_audio_timing.get("started_at")
                            if isinstance(started_at, (int, float)):
                                first_audio_s = started_at - start
                                print(f"[StackChanTTS] 文本就绪到设备开始播放 {first_audio_s:.2f}s。")
                        print(
                            f"[StackChanTTS] 第 {clips_played + 1} 条音频完成："
                            f"设备传输+播放 {elapsed:.2f}s，其中音频 {seconds:.2f}s。"
                        )
                        clips_played += 1
                        audio_secs += seconds
                        total_hw_secs += elapsed
                        waiting_pcm = []
                        waiting_bytes = 0
                        waiting_segments = 0
                        first_ready_at = None

                    while True:
                        if synth_done:
                            flush_audio()
                            if segments_seen != expected_segments:
                                raise RuntimeError(
                                    f"TTS segment count mismatch: "
                                    f"{segments_seen} != {expected_segments}"
                                )
                            print(
                                f"[StackChanTTS] 完成 {segments_seen} 段合成为 "
                                f"{clips_played} 条音频：实际音频 {audio_secs:.2f}s，"
                                f"设备传输+播放 {total_hw_secs:.2f}s，"
                                f"本轮总耗时 {time.perf_counter()-start:.2f}s。"
                            )
                            self.last_speech_timing = {
                                "text_to_audio_s": first_audio_s if first_audio_s is not None else -1.0,
                                "audio_s": audio_secs,
                                "total_s": time.perf_counter() - start,
                                "segments": segments_seen,
                                "clips": clips_played,
                            }
                            return clips_played > 0

                        if waiting_pcm and first_ready_at is not None:
                            remaining = prefetch - (time.perf_counter() - first_ready_at)
                            if remaining <= 0:
                                flush_audio()
                                continue
                            wait_for = max(0.01, remaining)
                        else:
                            wait_for = 90.0
                        try:
                            ev = self._messages.get(timeout=wait_for)
                        except queue.Empty as exc:
                            if waiting_pcm:
                                flush_audio()
                                continue
                            raise TimeoutError(
                                "TTS worker 90 秒未输出；已保留文字回复"
                            ) from exc

                        kind = ev.get("event")
                        if kind == "ready":
                            print(
                                "[StackChanTTS] 模型已就绪，加载耗时 "
                                f"{ev.get('model_load_s')}s"
                            )
                            continue
                        if kind in ("fatal", "worker_exited"):
                            raise RuntimeError(f"TTS worker exited: {ev}")
                        if ev.get("id") != request_id:
                            continue
                        if kind == "error":
                            raise RuntimeError(str(ev.get("error", "TTS worker error")))
                        if kind == "done":
                            synth_done = True
                            expected_segments = int(ev.get("segments", -1))
                            continue
                        if kind != "segment":
                            continue

                        expected = Path(
                            output_dir, f"segment_{segments_seen:03d}.pcm"
                        ).resolve()
                        actual = Path(str(ev.get("path", ""))).resolve()
                        if actual != expected:
                            raise RuntimeError(f"Unexpected PCM segment path: {actual}")
                        raw_pcm = actual.read_bytes()
                        actual.unlink(missing_ok=True)
                        if len(raw_pcm) != int(ev.get("bytes", -1)):
                            raise RuntimeError("PCM size does not match producer event")
                        if not raw_pcm or len(raw_pcm) > max_pcm_bytes or len(raw_pcm) % 2:
                            raise RuntimeError("Invalid 16kHz PCM16 segment")

                        seconds = len(raw_pcm) / 32000.0
                        segments_seen += 1
                        print(
                            f"[StackChanTTS] 第 {segments_seen} 段已合成："
                            f"音频 {seconds:.2f}s，距回复打印 "
                            f"{time.perf_counter()-start:.2f}s。"
                        )
                        if waiting_pcm and waiting_bytes + len(raw_pcm) > max_pcm_bytes:
                            # A single command can play at most 12 seconds.
                            flush_audio()
                        if not waiting_pcm:
                            first_ready_at = time.perf_counter()
                        waiting_pcm.append(raw_pcm)
                        waiting_bytes += len(raw_pcm)
                        waiting_segments += 1
                        if waiting_bytes == max_pcm_bytes:
                            flush_audio()
            except (OSError, ValueError, RuntimeError, TimeoutError,
                    subprocess.SubprocessError, AssertionError) as exc:
                print(f"[StackChanTTS] 语音暂不可用，仍保留文字回复：{exc}")
                self._stop_worker()
                return False

    def _stop_worker(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                try:
                    proc.stdin.write(b'{"cmd":"shutdown"}\n')
                    proc.stdin.flush()
                except (OSError, ValueError):
                    pass
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            if proc.stdin is not None:
                proc.stdin.close()
            if proc.stdout is not None:
                proc.stdout.close()

    def close(self) -> None:
        with self._speech_lock:
            if self._closed:
                return
            self._closed = True
            self._stop_worker()
