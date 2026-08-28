"""Browser-facing media derivatives.

The source video is AV1, which most browsers cannot decode, so editing runs
against a small H.264 proxy. Everything here writes to editor_cache/ only.
"""
from __future__ import annotations

import array
import json
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

PROXY_HEIGHT = 640
WAVEFORM_BUCKETS = 2400
WAVEFORM_RATE = 8000


def _ffmpeg(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True)


def duration(video: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
        check=True, text=True, capture_output=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def _is_fresh(target: Path, source: Path) -> bool:
    return target.is_file() and target.stat().st_mtime >= source.stat().st_mtime


def ensure_proxy(video: Path, proxy: Path) -> Path:
    """Transcode a low-resolution, seekable H.264 copy for in-browser playback."""
    if _is_fresh(proxy, video):
        return proxy
    proxy.parent.mkdir(parents=True, exist_ok=True)
    partial = proxy.with_suffix(".partial.mp4")
    _ffmpeg([
        "ffmpeg", "-y", "-v", "error", "-i", str(video),
        "-vf", f"scale=-2:{PROXY_HEIGHT}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-g", "48", "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", str(partial),
    ])
    partial.replace(proxy)
    return proxy


FILM_EVERY = 5.0        # seconds between thumbnails
FILM_HEIGHT = 56        # tall enough to recognise a shot, small enough to be cheap


def ensure_filmstrip(video: Path, strip: Path, duration: float,
                     every: float = FILM_EVERY) -> dict[str, Any]:
    """One wide image holding a thumbnail every few seconds.

    A row of pictures answers "where am I" faster than a waveform does, and it
    is the same question the timeline exists to answer. One ffmpeg pass tiles
    them into a single file, so the browser fetches one image rather than sixty,
    and it is kept like the proxy: derived, cached, regenerated when the video
    changes.
    """
    tiles = max(1, int(duration // every) + 1)
    if not _is_fresh(strip, video):
        strip.parent.mkdir(parents=True, exist_ok=True)
        partial = strip.with_suffix(".partial.png")
        _ffmpeg([
            "ffmpeg", "-y", "-v", "error", "-i", str(video),
            "-vf", f"fps=1/{every},scale=-1:{FILM_HEIGHT},tile={tiles}x1",
            "-frames:v", "1", str(partial),
        ])
        partial.replace(strip)
    with Image.open(strip) as sheet:
        width, height = sheet.size
    return {"every": every, "tiles": tiles,
            "width": width // tiles, "height": height}


def ensure_waveform(video: Path, peaks_path: Path) -> list[float]:
    """Compute per-bucket peak amplitude (0..1) for the timeline display."""
    if _is_fresh(peaks_path, video):
        return json.loads(peaks_path.read_text(encoding="utf-8"))["peaks"]

    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vn",
         "-ac", "1", "-ar", str(WAVEFORM_RATE), "-f", "s16le", "-"],
        check=True, capture_output=True,
    )
    samples = array.array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    if not samples:
        return []

    size = max(1, len(samples) // WAVEFORM_BUCKETS)
    peaks = []
    for offset in range(0, len(samples), size):
        window = samples[offset:offset + size]
        peaks.append(round(max(max(window), -min(window)) / 32768, 4))

    peaks_path.parent.mkdir(parents=True, exist_ok=True)
    peaks_path.write_text(json.dumps({"peaks": peaks}), encoding="utf-8")
    return peaks


# The pipeline owns window extraction; re-export so both use one implementation.
from core.media import slice_audio  # noqa: E402,F401
