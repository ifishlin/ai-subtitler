from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from .utils import run


def _tool(name: str) -> str:
    """Prefer the copy installed beside the running interpreter.

    Running .venv/bin/python directly does not put .venv/bin on PATH, so a
    dependency installed into the virtualenv is invisible to a bare PATH lookup.
    """
    local = Path(sys.executable).parent / name
    if local.is_file():
        return str(local)
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"找不到 {name}，請執行 .venv/bin/python -m pip install -r requirements.txt")
    return found


def _metadata_context(info_path: Path) -> str:
    if not info_path.is_file():
        return ""
    info = json.loads(info_path.read_text(encoding="utf-8"))
    return f"標題：{info.get('title', '')}\n說明：{info.get('description', '')}"[:6000]


def prepare_video(source: str, work_dir: Path) -> tuple[Path, str]:
    work_dir.mkdir(parents=True, exist_ok=True)
    if source.startswith(("https://", "http://")):
        template = str(work_dir / "source_%(id)s.%(ext)s")
        command = [
            _tool("yt-dlp"),
            "--no-check-certificates",
            "--no-playlist",
            "-f", "bv*+ba/b",
            "--merge-output-format", "mp4",
            "--write-info-json",
            "--restrict-filenames",
            "-o", template,
            "--print", "after_move:filepath",
            source,
        ]
        result = subprocess.run(command, check=True, text=True, capture_output=True)
        video = Path(result.stdout.strip().splitlines()[-1]).resolve()
        return video, _metadata_context(video.with_suffix(".info.json"))

    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Video not found: {path}")
    return path, _metadata_context(path.with_suffix(".info.json"))


def extract_audio(video: Path, audio: Path) -> None:
    audio.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-i", str(video), "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio),
    ])


def slice_audio(source: Path, start: float, end: float, audio: Path) -> Path:
    """Extract one 16 kHz mono window for a second transcription pass."""
    audio.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{max(0.0, start):.3f}", "-to", f"{end:.3f}",
        "-i", str(source), "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio),
    ], check=True, capture_output=True, text=True)
    return audio


def duration(video: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
        check=True, text=True, capture_output=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])
