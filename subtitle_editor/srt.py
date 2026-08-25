"""SRT reading and writing.

Timestamp formatting is imported from the pipeline so the reviewed file is
byte-for-byte compatible with what render.py already consumes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.utils import timestamp

TIME_LINE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def _seconds(hours: str, minutes: str, secs: str, millis: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(millis) / 1000


def parse_srt(path: Path) -> list[dict[str, Any]]:
    """Return [{id, start, end, text}] from an SRT file."""
    segments: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        match = next((TIME_LINE.search(line) for line in lines if TIME_LINE.search(line)), None)
        if match is None:
            continue
        time_index = next(i for i, line in enumerate(lines) if TIME_LINE.search(line))
        text = " ".join(line.strip() for line in lines[time_index + 1:]).strip()
        segments.append({
            "id": len(segments) + 1,
            "start": _seconds(*match.groups()[:4]),
            "end": _seconds(*match.groups()[4:]),
            "text": text,
        })
    return segments


def write_srt(path: Path, segments: list[dict[str, Any]]) -> None:
    """Write segments as SRT, skipping empty text and renumbering from 1."""
    usable = [s for s in segments if str(s.get("text", "")).strip()]
    usable.sort(key=lambda s: (s["start"], s["end"]))
    blocks = [
        f"{index}\n"
        f"{timestamp(segment['start'], True)} --> {timestamp(segment['end'], True)}\n"
        f"{str(segment['text']).strip()}"
        for index, segment in enumerate(usable, start=1)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
