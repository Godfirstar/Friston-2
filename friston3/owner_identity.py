"""Owner enrollment, current-utterance evidence and local exhibit controls.

Biometric matches select a conversational relationship, never administrator
rights. Only an explicit command entered on the local console changes profiles.
There is no inference of real names from visitors and no raw-media persistence.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from uuid import uuid4

from friston3.config import PROJECT_ROOT
from friston3.vision.owner_face import OwnerFaceRecognizer
from stackchan_integration.owner_voice import OwnerVoiceRecognizer


def _number(name, default, low, high):
    try:
        value = float(os.getenv(name, str(default)))
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError
        return value
    except ValueError as exc:
        raise ValueError(f"{name} 必须在 {low}..{high} 之间") from exc


def _empty_evidence(reason="本轮没有可用观测"):
    return {"status": "not_observed", "reason": reason, "score": None}


def _cosine(left, right):
    if len(left) != len(right):
        return -1.0
    return sum(a * b for a, b in zip(left, right))


class OwnerProfile:
    """One atomic, local JSON file containing only enrollment vectors."""

    def __init__(self, path=None):
        configured = path or os.getenv("FRISTON_OWNER_PROFILE", "")
        self.path = Path(configured).expanduser() if configured else (
            PROJECT_ROOT / "data" / "runtime" / "owner_identity.json")
        if not self.path.is_absolute():
            self.path = PROJECT_ROOT / self.path
        self.data = {"version": 1, "owner_name": "可露希尔"}
        self.error = ""
        try:
            if self.path.exists():
                if self.path.stat().st_size > 1_000_000:
                    raise ValueError("主人模板文件大小异常")
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict) or data.get("version") != 1:
                    raise ValueError("主人模板版本或格式不支持")
                for name in ("face", "voice"):
                    if name in data:
                        self._validate(data[name])
                self.data = data
        except (OSError, ValueError, TypeError) as exc:
            self.error = str(exc)

    @staticmethod
    def _validate(record):
        if (not isinstance(record, dict) or not isinstance(record.get("model_id"), str)
                or not record["model_id"] or len(record["model_id"]) > 120):
            raise ValueError("模板缺少有效的模型标识")
        vectors = record.get("templates")
        if not isinstance(vectors, list) or not 3 <= len(vectors) <= 8:
            raise ValueError("每种主人模板需要 3..8 个有效样本")
        dimension = None
        for vector in vectors:
            if not isinstance(vector, list) or not 32 <= len(vector) <= 4096:
                raise ValueError("模板向量维度异常")
            if dimension is not None and len(vector) != dimension:
                raise ValueError("模板向量维度不一致")
            dimension = len(vector)
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
                raise ValueError("模板向量包含无效数值")
            if not 0.98 <= sum(v * v for v in vector) <= 1.02:
                raise ValueError("模板向量未归一化")

    def get(self, modality):
        return deepcopy(self.data.get(modality, {})) if not self.error else {}

    def save(self, updates):
        # A damaged profile is never silently overwritten by a partial enrollment.
        if self.error:
            raise ValueError(f"旧模板损坏：{self.error}；先在电脑端 /owner forget 后重新录入")
        data = deepcopy(self.data)
        for modality, record in updates.items():
            if modality not in ("face", "voice"):
                raise ValueError("未知模板种类")
            self._validate(record)
            data[modality] = record
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".owner-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.data = data

    def forget(self):
        self.path.unlink(missing_ok=True)
        self.data = {"version": 1, "owner_name": "可露希尔"}
        self.error = ""


@dataclass
class _Utterance:
    pcm: bytes
    sample_rate: int
    started: float = field(default_factory=time.monotonic)
    face: dict = field(default_factory=_empty_evidence)
    voice: dict = field(default_factory=_empty_evidence)
    face_done: threading.Event = field(default_factory=threading.Event)
    voice_done: threading.Event = field(default_factory=threading.Event)


class OwnerIdentityManager:
    """Bind new evidence to each utterance; never keep a previous owner match."""

    def __init__(self, camera=None, device=None):
        self.camera, self.device = camera, device
        self.profile = OwnerProfile()
        self.face, self.voice = OwnerFaceRecognizer(), OwnerVoiceRecognizer()
        self.enabled = os.getenv("FRISTON_OWNER_IDENTITY", "1").strip() != "0"
        self.face_enabled = os.getenv("FRISTON_OWNER_FACE", "1").strip() != "0"
        self.face_as_speaker = os.getenv("FRISTON_OWNER_FACE_AS_SPEAKER", "0").strip() == "1"
        self.wait_budget = _number("FRISTON_OWNER_WAIT_SECONDS", 0.65, 0, 3)
        self.camera_budget = _number("FRISTON_OWNER_CAMERA_SECONDS", 2.5, 0.5, 8)
        self.guest_ttl = _number("FRISTON_VISITOR_IDLE_SECONDS", 45, 5, 300)
        self.mode = "auto"
        self._mode_until = 0.0
        self._owner_once = False
        self._once_until = 0.0
        self._ticket = None
        self._busy = {"face": False, "voice": False}
        self._retry_after = {"face": 0.0, "voice": 0.0}
        self._lock = threading.Lock()
        self._closed = False
        self._visitor_id = ""
        self._last_visitor = 0.0
        self._last_role = "unknown"
        self.last_context = {}

    def warmup(self):
        """Only previously enrolled modalities are loaded; no network operations."""
        if not self.enabled:
            return
        for name, recognizer in (("face", self.face), ("voice", self.voice)):
            if (name == "face" and not self.face_enabled) or not self.profile.get(name):
                continue
            state = recognizer.preload()
            print(f"[身份] {name}：{state['status']} {state.get('reason', '')}")

    def invalidate(self):
        # Existing workers can finish cleanup, but their ticket will never be used.
        self._ticket = None

    def _start_probe(self, ticket, name, action):
        event = getattr(ticket, name + "_done")
        with self._lock:
            if self._closed or self._busy[name] or time.monotonic() < self._retry_after[name]:
                setattr(ticket, name, _empty_evidence("识别忙碌或设备正在恢复；本轮不沿用旧身份"))
                event.set()
                return
            self._busy[name] = True

        def run():
            try:
                result = action()
                if not isinstance(result, dict):
                    raise ValueError("身份识别返回格式异常")
            except Exception as exc:
                result = {"status": "error", "reason": f"{type(exc).__name__}: {exc}", "score": None}
            # Commit result and cooldown before another utterance can submit
            # work. Late results still belong only to their abandoned ticket.
            with self._lock:
                if result.get("status") in ("error", "unavailable"):
                    self._retry_after[name] = time.monotonic() + 30
                setattr(ticket, name, result)
                self._busy[name] = False
            event.set()

        threading.Thread(target=run, name=f"FristonOwner-{name}", daemon=True).start()

    def _frame(self, *, timeout=None):
        if self.camera is None:
            raise RuntimeError("没有接入摄像头")
        return self.camera.snapshot_for_identity(timeout=self.camera_budget if timeout is None else timeout)

    def begin_utterance(self, pcm, sample_rate=16000):
        """Called after MIC transfer, before ASR. No audio is written to disk."""
        self.invalidate()
        self._expire_mode()
        ticket = _Utterance(pcm=pcm, sample_rate=sample_rate)
        self._ticket = ticket
        face_record, voice_record = self.profile.get("face"), self.profile.get("voice")
        if not self.enabled or self.mode != "auto" or self._owner_once:
            ticket.face_done.set()
            ticket.voice_done.set()
            return
        if voice_record and self.voice.status().get("loaded"):
            self._start_probe(ticket, "voice", lambda: self.voice.observe(
                pcm, voice_record["templates"], sample_rate, model_id=voice_record["model_id"]))
        else:
            ticket.voice = _empty_evidence("声纹未录入、未就绪或已关闭")
            ticket.voice_done.set()
        if (face_record and self.face_enabled and self.camera is not None
                and self.face.status()["status"] == "ready"):
            if hasattr(self.camera, "device"):
                # StackChan owns serial I/O in one scheduler. ASR can run while
                # this bounded atomic capture/release job is in flight.
                self._start_probe(ticket, "face", lambda: self.face.observe(
                    self._frame(), face_record["templates"], model_id=face_record["model_id"]))
            else:
                # Native VideoCapture calls cannot be cancelled by a Python
                # timeout. Keep PC capture serial, before ASR / photo skills.
                try:
                    frame = self._frame()
                    self._start_probe(ticket, "face", lambda: self.face.observe(
                        frame, face_record["templates"], model_id=face_record["model_id"]))
                except Exception as exc:
                    ticket.face = {"status": "error", "reason": str(exc), "score": None}
                    ticket.face_done.set()
        else:
            ticket.face = _empty_evidence("人脸未录入、未就绪或已关闭")
            ticket.face_done.set()

    def new_visitor(self):
        self._visitor_id = "visitor:" + uuid4().hex[:12]
        self._last_visitor = time.monotonic()
        return self._visitor_id

    def handover_visitor(self):
        """An explicit handover also ends any pending owner demonstration."""
        self.set_mode("visitor" if self.mode == "visitor" else "auto")
        return self._visitor_id

    def set_mode(self, mode):
        if mode not in ("auto", "owner", "visitor", "once"):
            raise ValueError("模式只能是 auto、owner、visitor、once")
        self.invalidate()
        self._owner_once = mode == "once"
        self._once_until = time.monotonic() + 30 if mode == "once" else 0
        self.mode = "auto" if mode == "once" else mode
        # Manual owner mode is an explicit local exhibition session, not a
        # sticky recognition result. The timeout cannot be extended by speech.
        self._mode_until = time.monotonic() + 120 if mode == "owner" else 0
        self.new_visitor()

    def _expire_mode(self):
        if self.mode == "owner" and time.monotonic() >= self._mode_until:
            self.mode = "auto"
        if self._owner_once and time.monotonic() >= self._once_until:
            self._owner_once = False

    def context_for(self, *, input_source, pcm=None, sample_rate=16000):
        started = time.monotonic()
        self._expire_mode()
        owner_once, self._owner_once = self._owner_once, False
        face, voice = _empty_evidence(), _empty_evidence()
        role, source, reason = "unknown", "unverified", "本轮身份没有确认"
        local = input_source == "text"
        if self.mode == "visitor":
            role, source, reason = "visitor", "local_session", "电脑端指定接待来访者"
        elif local:
            role, source, reason = "owner", "local_console", "主人在自己的电脑控制台输入"
        elif self.mode == "owner" or owner_once:
            role, source, reason = "owner", "local_session", "电脑端明确指定的主人演示回合"
        elif self.enabled and pcm:
            ticket = self._ticket
            if ticket is None or ticket.pcm is not pcm or ticket.sample_rate != sample_rate:
                # No reuse across independently captured or rejected recordings.
                self.begin_utterance(pcm, sample_rate)
                ticket = self._ticket
            deadline = started + self.wait_budget
            for event in (ticket.voice_done, ticket.face_done):
                event.wait(timeout=max(0, deadline - time.monotonic()))
            face = deepcopy(ticket.face) if ticket.face_done.is_set() else _empty_evidence("本轮人脸观测超时")
            voice = deepcopy(ticket.voice) if ticket.voice_done.is_set() else _empty_evidence("本轮声纹观测超时")
            if voice.get("status") == "match":
                role, source, reason = "owner", "voice", "本轮声音与主人模板匹配"
                if face.get("owner_visible"):
                    source = "face_voice"
            elif voice.get("status") == "mismatch":
                role, source, reason = "visitor", "voice", "本轮声音与主人模板不匹配"
            elif (self.face_as_speaker and face.get("status") == "match"
                  and face.get("face_count") == 1):
                role, source, reason = "owner", "face_assumption", "已启用单人正对镜头假设；未做唇音同步"
            elif face.get("owner_visible"):
                reason = "看见主人，但无法确认当前声音是否来自主人"
        if role == "owner":
            profile_id = "owner"
        else:
            if (not self._visitor_id or started - self._last_visitor > self.guest_ttl
                    or self._last_role == "owner"):
                self.new_visitor()
            profile_id = self._visitor_id
            self._last_visitor = started
        self._last_role = role
        context = {
            "role": role, "profile_id": profile_id,
            "display_name": "可露希尔" if role == "owner" else "来访者",
            "source": source, "authenticated": role == "owner",
            "local_operator": local and role == "owner",
            "owner_visible": bool(face.get("owner_visible")), "reason": reason,
            "scores": {"face": face.get("score"), "voice": voice.get("score")},
            "observations": {"face": face, "voice": voice},
            "wait_ms": round((time.monotonic() - started) * 1000, 1),
        }
        self.last_context = deepcopy(context)
        self.invalidate()
        return context

    def status_text(self):
        self._expire_mode()
        lines = [f"身份模式：{self.mode}；单人看脸推断说话人：{'开' if self.face_as_speaker else '关'}",
                 f"主人模板：{self.profile.path}"]
        if self._owner_once:
            lines.append(f"下一轮主人演示待用，{max(0, self._once_until-time.monotonic()):.0f} 秒后失效")
        elif self.mode == "owner":
            lines.append(f"主人演示还剩 {max(0, self._mode_until-time.monotonic()):.0f} 秒")
        if self.profile.error:
            lines.append(f"模板读取失败：{self.profile.error}")
        for name, recognizer in (("face", self.face), ("voice", self.voice)):
            record, state = self.profile.get(name), recognizer.status()
            lines.append(f"{name}：{len(record.get('templates', []))} 个样本；{state['status']} {state.get('reason', '')}")
        if self.last_context:
            lines.append("上一轮身份依据：" + json.dumps(self.last_context, ensure_ascii=False, indent=2))
        return "\n".join(lines)

    def enroll(self, modality="both"):
        if modality not in ("face", "voice", "both"):
            raise ValueError("录入种类只能是 face、voice、both")
        if self.profile.error:
            raise ValueError(f"旧模板读取失败：{self.profile.error}；先 /owner forget 再录入")
        # Wait for already submitted work before touching either device/model.
        self.invalidate()
        with self._lock:
            if any(self._busy.values()):
                raise RuntimeError("上一轮身份识别尚在释放资源；稍候再录入")
        updates = {}
        for name in (("face", "voice") if modality == "both" else (modality,)):
            recognizer = self.face if name == "face" else self.voice
            state = recognizer.preload()
            if state.get("status") not in ("ready", "ok"):
                raise RuntimeError(state.get("reason", f"{name} 模型未就绪"))
            count = 5 if name == "face" else 3
            samples = []
            model_id = ""
            print(f"[主人录入] {name} 需要 {count} 个样本；只让你本人进入镜头/说话。输入 q 可取消。")
            if name == "face":
                print("依次正面、微向左、微向右、稍远、稍近；保持整张脸清晰。建议穿好妆造后录入。")
            else:
                print("每次轻触屏幕后正常说 3–6 秒，三次用不同句子；保持现场安静。")
            attempts = 0
            while len(samples) < count and attempts < count * 4:
                if input(f"[{name} {len(samples)+1}/{count}] 就位后按 Enter：").strip().lower() == "q":
                    print("已取消；原模板保留。")
                    return False
                attempts += 1
                if name == "face":
                    frame = self._frame(timeout=8.0)
                    extracted = recognizer.extract(frame)
                    del frame
                else:
                    if self.device is None:
                        raise RuntimeError("声纹录入需要栈酱麦克风")
                    pcm = self.device.record_pcm(mode="touch", wait_ms=10000, max_ms=8000,
                                                 silence_ms=900, threshold=350)
                    extracted = recognizer.extract(pcm, sample_rate=16000)
                    del pcm
                if extracted.get("status") != "ok":
                    print("未采用：" + extracted.get("reason", extracted.get("status", "未知错误")))
                    continue
                vector = extracted["embedding"]
                current_model = extracted["model_id"]
                if model_id and current_model != model_id:
                    raise RuntimeError("录入期间模型发生变化，请重新开始")
                # All samples must resemble the first; cannot enroll several
                # different people by taking a maximum over unrelated templates.
                threshold = self.face.threshold if name == "face" else self.voice.threshold
                if samples and _cosine(samples[0], vector) < threshold:
                    print("未采用：与第一个样本不够一致，请保持同一人、清晰正面/正常音量重试。")
                    continue
                model_id = current_model
                samples.append(vector)
                print(f"已采用 {len(samples)}/{count}")
            if len(samples) < count:
                raise RuntimeError("有效样本不足；旧模板未修改。改善光线/距离/收音后重新录入。")
            updates[name] = {"model_id": model_id, "templates": samples}
        self.profile.save(updates)
        print("主人模板已更新，只保存特征向量；原始照片与录音未写入文件。")
        return True

    def close(self):
        self._closed = True
        self.invalidate()
