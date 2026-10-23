"""Explicit one-time owner-model download, separate from offline runtime.

Only the CLI setup action should call ``download_owner_models``. Importing this
module, starting dialogue, or enrolling a template never calls the network.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from friston3.config import MODELS_DIR, PROJECT_ROOT, YUNET_MODEL_PATH


_SFACE_NAME = "face_recognition_sface_2021dec.onnx"
_SFACE_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
_SFACE_SIZE = 38696353
_SFACE_URLS = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/face_recognition_sface/" + _SFACE_NAME,
    "https://huggingface.co/opencv/face_recognition_sface/resolve/main/" + _SFACE_NAME,
)
_VOICE_REPO = "iic/speech_campplus_sv_zh-cn_16k-common"
_VOICE_WEIGHT = "campplus_cn_common.bin"
_VOICE_FILES = ("config.yaml", "configuration.json", _VOICE_WEIGHT)
# Official funasr/campplus commit 0817bacbf3f87a85962e3b7bbe231ff3c606f404.
_VOICE_SHA256 = "3388cf5fd3493c9ac9c69851d8e7a8badcfb4f3dc631020c4961371646d5ada8"
_VOICE_SIZE = 28036335


def _path(setting, default):
    value = os.getenv(setting, "").strip().strip('"')
    path = Path(value).expanduser() if value else default
    return path if path.is_absolute() else PROJECT_ROOT / path


def _missing_modules(specs):
    missing = []
    for module, package in specs:
        try:
            available = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            available = False
        if not available:
            missing.append(package)
    return missing


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _matches(path, expected_size, expected_hash):
    try:
        return (path.is_file() and path.stat().st_size == expected_size
                and _digest(path) == expected_hash)
    except OSError:
        return False


def _download_verified(urls, destination, expected_size, expected_hash):
    """Stream into a temporary .part file; publish only a verified whole file."""
    if _matches(destination, expected_size, expected_hash):
        return "existing"
    destination.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for index, url in enumerate(urls, start=1):
        descriptor, temp_name = tempfile.mkstemp(
            prefix=destination.name + ".", suffix=".part", dir=destination.parent)
        part = Path(temp_name)
        try:
            started = time.monotonic()
            request = urllib.request.Request(url, headers={"User-Agent": "Friston-3-Owner-Setup/1.0"})
            print(f"[OwnerSetup] 下载 {destination.name}（官方源 {index}/{len(urls)}）…", flush=True)
            with os.fdopen(descriptor, "wb") as output:
                with urllib.request.urlopen(request, timeout=30) as response:
                    total = 0
                    digest = hashlib.sha256()
                    while True:
                        if time.monotonic() - started > 600:
                            raise TimeoutError("本次下载超过 10 分钟，请检查网络后重试")
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > expected_size:
                            raise ValueError("下载大小超过官方文件大小，未覆盖本地文件")
                        digest.update(block)
                        output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
            if total != expected_size or digest.hexdigest() != expected_hash:
                raise ValueError("下载文件的大小或 SHA256 与官方权重不一致")
            os.replace(part, destination)
            return "downloaded"
        except Exception as error:
            errors.append(f"源 {index}: {type(error).__name__}: {error}")
        finally:
            part.unlink(missing_ok=True)
    raise RuntimeError("；".join(errors))


def _copy_atomic(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".part", dir=destination.parent)
    part = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        os.replace(part, destination)
    finally:
        part.unlink(missing_ok=True)


def _voice_bundle_valid(directory):
    """Check known weights and plain configuration without importing torch."""
    if not _matches(directory / _VOICE_WEIGHT, _VOICE_SIZE, _VOICE_SHA256):
        return False
    try:
        import yaml
        config = yaml.safe_load((directory / "config.yaml").read_text(encoding="utf-8-sig"))
        metadata = json.loads((directory / "configuration.json").read_text(encoding="utf-8-sig"))
        return (
            isinstance(config, dict) and config.get("model") == "CAMPPlus"
            and isinstance(config.get("model_conf"), dict)
            and config["model_conf"].get("embedding_size") == 192
            and config["model_conf"].get("output_level", "segment") == "segment"
            and (config.get("frontend_conf") or {}).get("fs", 16000) == 16000
            and isinstance(metadata, dict)
            and metadata.get("file_path_metas", {}).get("init_param") == _VOICE_WEIGHT
        )
    except (ImportError, OSError, ValueError, TypeError, AttributeError):
        return False
    except Exception:
        # PyYAML parser errors have their own type; malformed local config is
        # simply an incomplete bundle, never authority to execute model code.
        return False


def _prepare_face():
    target = _path("FRISTON_OWNER_FACE_MODEL", MODELS_DIR / _SFACE_NAME)
    result = {"component": "face", "path": str(target), "success": False, "ready": False}
    missing = _missing_modules((("cv2", '"opencv-python==4.11.0.86"'),
                                ("numpy", '"numpy==1.26.4"')))
    if missing:
        result["dependency_command"] = "python -m pip install " + " ".join(missing)
    try:
        result["status"] = _download_verified(_SFACE_URLS, target, _SFACE_SIZE, _SFACE_SHA256)
        result["success"] = True
        yunet_ready = YUNET_MODEL_PATH.is_file() and YUNET_MODEL_PATH.stat().st_size >= 200_000
        result["ready"] = not missing and yunet_ready
        if not yunet_ready:
            result["reason"] = f"SFace 已就绪；缺少已有摄影功能使用的 YuNet：{YUNET_MODEL_PATH}"
        elif missing:
            result["reason"] = "模型已就绪；请先安装列出的运行依赖"
        else:
            result["reason"] = "SFace 校验通过；已有 YuNet 文件和 Python 依赖可用"
    except Exception as error:
        result.update(status="error", reason=f"{type(error).__name__}: {error}")
    return result


def _prepare_voice():
    target = _path("FRISTON_OWNER_VOICE_MODEL_DIR", MODELS_DIR / "campplus")
    result = {"component": "voice", "path": str(target), "success": False, "ready": False}
    specs = (("funasr", '"funasr==1.4.16"'), ("yaml", "PyYAML"),
             ("numpy", '"numpy==1.26.4"'))
    other_missing = _missing_modules(specs)
    torch_missing = _missing_modules((("torch", "torch"), ("torchaudio", "torchaudio")))
    missing = other_missing + torch_missing
    valid = _voice_bundle_valid(target)
    download_missing = _missing_modules((("modelscope", "modelscope"), ("yaml", "PyYAML"))) if not valid else []
    packages = list(dict.fromkeys(other_missing + download_missing))
    commands = []
    if len(torch_missing) == 2 and (3, 10) <= sys.version_info[:2] <= (3, 12):
        commands.append(
            'python -m pip install "torch==2.5.1" "torchaudio==2.5.1" '
            '--index-url https://download.pytorch.org/whl/cpu')
    elif torch_missing:
        if len(torch_missing) == 1:
            installed = "torch" if torch_missing[0] == "torchaudio" else "torchaudio"
            result["dependency_note"] = (
                f"检测到已有 {installed}，缺少 {torch_missing[0]}；"
                "请按已有包的版本及 CPU/CUDA 构建补齐配套包，不要单独安装另一项的最新版本。")
        else:
            result["dependency_note"] = (
                f"当前 Python {sys.version_info.major}.{sys.version_info.minor} 尚无 torch/torchaudio；"
                "请安装支持该 Python 版本的同版本 CPU 配套组合，或在已有 Python 3.10–3.12 运行环境启用声纹。")
    if packages:
        commands.append("python -m pip install " + " ".join(packages))
    if commands:
        result["dependency_commands"] = commands
        result["dependency_command"] = commands[0]
    if download_missing:
        result.update(status="missing_dependency", reason="下载所需依赖未安装；执行给出的命令后重试")
        return result
    try:
        if valid:
            result["status"] = "existing"
        else:
            from modelscope.hub.snapshot_download import snapshot_download
            target.parent.mkdir(parents=True, exist_ok=True)
            print(f"[OwnerSetup] 从 ModelScope 下载 {_VOICE_REPO}（仅三个必要文件）…", flush=True)
            # SDK owns its network retries and hash checks. Stage its cache and
            # partial transfers outside the final model directory, then remove
            # that temporary tree even when interrupted or download fails.
            with tempfile.TemporaryDirectory(prefix=".owner-campplus-", dir=target.parent) as stage:
                staged = Path(stage) / "snapshot"
                snapshot_download(
                    model_id=_VOICE_REPO,
                    local_dir=str(staged),
                    allow_file_pattern=list(_VOICE_FILES),
                )
                if not _voice_bundle_valid(staged):
                    raise ValueError(
                        "下载的 CAM++ 配置或 SHA256 未通过校验；保留了现有模型。"
                        "请确认使用 iic/speech_campplus_sv_zh-cn_16k-common 原版权重。")
                for name in _VOICE_FILES:
                    _copy_atomic(staged / name, target / name)
            result["status"] = "downloaded"
        result.update(success=True, ready=not missing,
                      reason="CAM++ 权重与配置校验通过" if not missing else "模型已就绪；请先安装列出的运行依赖")
    except Exception as error:
        result.update(status="error", reason=f"{type(error).__name__}: {error}")
    return result


def download_owner_models(selection="all"):
    """Download selected official models only after an explicit setup command.

    ``success`` describes the requested model files. ``runtime_ready`` also
    requires existing YuNet and installed module names; actual imports/model
    loading are checked later by the runtime. No dependency installation runs.
    """
    selection = str(selection).strip().lower()
    if selection not in ("face", "voice", "all"):
        raise ValueError("模型下载选项只能是 face、voice 或 all")
    print("[OwnerSetup] 现在显式联网准备模型；之后对话与身份识别使用本地文件。", flush=True)
    results = []
    for component, prepare in (("face", _prepare_face), ("voice", _prepare_voice)):
        if selection not in ("all", component):
            continue
        result = prepare()
        results.append(result)
        print(f"[OwnerSetup] {component}: {result['status']}；{result.get('reason', '')}", flush=True)
        print(f"  路径：{result['path']}", flush=True)
        for command in result.get("dependency_commands", [result.get("dependency_command")]):
            if command:
                print(f"  安装依赖：{command}", flush=True)
        if result.get("dependency_note"):
            print(f"  依赖说明：{result['dependency_note']}", flush=True)
    commands = list(dict.fromkeys(
        command for item in results
        for command in item.get("dependency_commands", [item.get("dependency_command")])
        if command))
    return {"success": all(item["success"] for item in results),
            "runtime_ready": all(item["ready"] for item in results),
            "selection": selection, "results": results,
            "dependency_commands": commands}
