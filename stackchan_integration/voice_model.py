"""Explicit model setup outside the project tree; no network during dialogue.

  python -m stackchan_integration.voice_model install
  python -m stackchan_integration.voice_model install --archive C:\\Downloads\\kokoro-int8-multi-lang-v1_1.tar.bz2
  python -m stackchan_integration.voice_model status

The one model comes from the official sherpa-onnx release. A local archive
can be imported before an offline event; it contains the same fixed model.
"""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request

from .local_tts import BILINGUAL_MODEL_NAME, default_bilingual_dir, kokoro_missing, model_root


MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
    "kokoro-int8-multi-lang-v1_1.tar.bz2"
)


def _download(destination: Path) -> None:
    print("Downloading the official bilingual voice. This is only needed once.", flush=True)
    print(MODEL_URL, flush=True)
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Friston3-Voice-Setup/1"})
    maximum = 700 * 1024 * 1024
    with urllib.request.urlopen(request, timeout=45) as response, destination.open("wb") as out:
        expected = int(response.headers.get("Content-Length", "0") or "0")
        if expected > maximum:
            raise ValueError("Unexpected archive size; download rejected")
        received, last_report = 0, -1
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            received += len(block)
            if received > maximum:
                raise ValueError("Unexpected archive size; download rejected")
            out.write(block)
            report = received // (10 * 1024 * 1024)
            if report != last_report:
                total = f" / {expected / 1024**2:.1f} MiB" if expected else " MiB"
                print(f"Downloaded {received / 1024**2:.1f}{total}", flush=True)
                last_report = report
        if expected and received != expected:
            raise OSError(f"Incomplete download: received {received} of {expected} bytes")
        if received == 0:
            raise OSError("Empty model download")


def _extract(archive: Path, destination: Path) -> Path:
    """Extract only regular files/directories, never links or absolute paths."""
    count, unpacked = 0, 0
    with tarfile.open(archive, "r:*") as bundle:
        for member in bundle:
            count += 1
            unpacked += max(0, member.size)
            if count > 10000 or unpacked > 1500 * 1024 * 1024:
                raise ValueError("Unexpected model archive contents")
            path = PurePosixPath(member.name)
            parts = path.parts
            if path.is_absolute() or ".." in parts or any(":" in p or "\\" in p for p in parts):
                raise ValueError("Unsafe path in model archive")
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError("Model archive contains a link or unsupported special file")
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, target.open("wb") as out:
                shutil.copyfileobj(source, out, length=1024 * 1024)
    for folder in (destination / BILINGUAL_MODEL_NAME, destination):
        if not kokoro_missing(folder):
            return folder
    raise ValueError("Archive lacks the complete Kokoro bilingual model, voices, lexicons or data folders")


def install(archive: Path | None = None) -> Path:
    target = (model_root() / BILINGUAL_MODEL_NAME).resolve()
    if not kokoro_missing(target):
        print(f"Bilingual voice is already installed: {target}")
        return target
    existing = default_bilingual_dir().resolve()
    if existing != target and not kokoro_missing(existing):
        # The consolidation installer owns migration and deletion. Locating a
        # model here must not move a symlink target or duplicate its weights.
        print(f"已发现完整 Kokoro 模型，无需再次下载：{existing}")
        return existing
    if target.exists():
        raise FileExistsError(f"模型目录不完整：{target}。请先完成该目录的清理，再重试安装。")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Same filesystem as target makes the final rename atomic.
    with tempfile.TemporaryDirectory(prefix=".friston_voice_", dir=target.parent) as staging:
        stage = Path(staging)
        if archive is None:
            archive = stage / "voice.tar.bz2"
            _download(archive)
        else:
            archive = archive.expanduser().resolve()
            if not archive.is_file():
                raise FileNotFoundError(f"Archive not found: {archive}")
        unpacked = stage / "unpacked"
        unpacked.mkdir()
        complete = _extract(archive, unpacked)
        if target.exists():
            raise FileExistsError(f"Destination appeared during install: {target}")
        complete.rename(target)
    print(f"Installed bilingual voice: {target}")
    print("模型已就绪；直接启动 main.py，使用固定中英音源。")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Install or locate Friston-3's offline Chinese/English voice")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install", help="Explicit one-time download (or local archive import)")
    setup.add_argument("--archive", type=Path, help="Use a previously downloaded official tar.bz2, no network")
    commands.add_parser("status", help="Check files only; does not synthesize or open the device")
    args = parser.parse_args()
    try:
        if args.command == "install":
            install(args.archive)
        else:
            folder = default_bilingual_dir()
            missing = kokoro_missing(folder)
            print(f"Bilingual model: {folder}")
            print("Ready (model files found)." if not missing else "Not installed or incomplete.")
            if missing:
                print("Run: python -m stackchan_integration.voice_model install")
    except (OSError, ValueError, tarfile.TarError, urllib.error.URLError) as exc:
        parser.exit(1, f"Voice setup failed: {exc}\nIf downloading is unavailable, use --archive with the official tar.bz2.\n")


if __name__ == "__main__":
    main()
