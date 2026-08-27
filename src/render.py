from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import run


def _filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def _style(srt: Path) -> str:
    """Bilingual cues are two lines, so they need a smaller face and more room
    below them than a single line of the spoken language."""
    bilingual = "bilingual" in srt.name
    size = 20 if bilingual else 24
    margin = 54 if bilingual else 70
    return (
        f"FontName=PingFang TC,FontSize={size},PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
        f"MarginV={margin},Alignment=2"
    )


def render(
    video: Path,
    srt: Path,
    visuals: list[dict[str, Any]],
    output: Path,
    progress: Path | None = None,
) -> None:
    """Burn `srt` and any visuals onto `video`. With `progress`, ffmpeg writes
    its own position to that file, which callers can read to show how far a
    long burn has got."""
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-y", "-i", str(video)]
    for visual in visuals:
        command.extend(["-loop", "1", "-i", visual["file"]])

    style = _style(srt)
    filters = [f"[0:v]subtitles=filename='{_filter_path(srt)}':force_style='{style}'[v0]"]
    previous = "v0"
    for index, visual in enumerate(visuals, start=1):
        current = f"v{index}"
        filters.append(
            f"[{index}:v]format=rgba[card{index}];"
            f"[{previous}][card{index}]overlay=0:0:enable='between(t,{visual['start']},{visual['end']})'[{current}]"
        )
        previous = current

    if progress:
        command.extend(["-progress", str(progress), "-nostats"])
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", f"[{previous}]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-shortest", str(output),
    ])
    run(command)
