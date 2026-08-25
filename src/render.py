from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import run


def _filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def render(video: Path, srt: Path, visuals: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-i", str(video)]
    for visual in visuals:
        command.extend(["-loop", "1", "-i", visual["file"]])

    style = "FontName=PingFang TC,FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=70,Alignment=2"
    filters = [f"[0:v]subtitles=filename='{_filter_path(srt)}':force_style='{style}'[v0]"]
    previous = "v0"
    for index, visual in enumerate(visuals, start=1):
        current = f"v{index}"
        filters.append(
            f"[{index}:v]format=rgba[card{index}];"
            f"[{previous}][card{index}]overlay=0:0:enable='between(t,{visual['start']},{visual['end']})'[{current}]"
        )
        previous = current

    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", f"[{previous}]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-shortest", str(output),
    ])
    run(command)
