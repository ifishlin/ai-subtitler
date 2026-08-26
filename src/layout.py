"""Place the video inside a larger frame, leaving room for explanation.

The picture moves to the upper left at a little over half its size and the rest
of the frame becomes a pale blue field: a right-hand column and a band beneath
the video, which is where captions and, later, explanatory text go.

Subtitles are burned after the composite rather than before, so they keep their
full size instead of being shrunk with the picture, and sit in the band under
the video where there is space for two lines.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .render import _filter_path
from .utils import run

CANVAS = (1920, 1080)
BACKGROUND = "0xDCE9F5"     # pale blue, dark enough for white text to sit on
INSET_SCALE = 0.60          # of each dimension: reads as about half the frame
MARGIN = 48                 # breathing room at the top and left edges
CAPTION_GAP = 28           # between the picture and the field below it

# libass measures MarginV in script coordinates, not pixels: an SRT defaults to
# PlayResY 288, so on a 1080-line frame every margin is scaled by 3.75, and a
# value given in pixels lands off-frame with no error raised. The margin also
# positions the bottom of the text block, so a caption sits above the value --
# derive it from where the text should end, not where it should begin.
SCRIPT_SCALE = CANVAS[1] / 288
CAPTION_BOTTOM = 0.82      # of frame height: inside the field, clear of the video


def geometry(scale: float = INSET_SCALE) -> dict[str, int]:
    """Where the picture sits, and where captions go beneath it."""
    width = int(CANVAS[0] * scale) // 2 * 2
    height = int(CANVAS[1] * scale) // 2 * 2
    bottom = MARGIN + height
    return {
        "width": width, "height": height, "x": MARGIN, "y": MARGIN,
        # Where the caption block ends, converted to script coordinates.
        "caption_margin": int(CANVAS[1] * (1 - CAPTION_BOTTOM) / SCRIPT_SCALE),
        "column_x": MARGIN + width + MARGIN,
        "column_width": CANVAS[0] - (MARGIN * 2 + width) - MARGIN,
        "band_y": bottom + CAPTION_GAP,
    }


def _style(srt: Path, margin: int) -> str:
    """White on a black outline: legible on the pale field and on the picture,
    should a long caption ever reach up into it."""
    bilingual = "bilingual" in srt.name
    size = 26 if bilingual else 30
    return (
        f"FontName=PingFang TC,FontSize={size},PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,"
        f"MarginV={margin},Alignment=2"
    )


def render_inset(
    video: Path,
    srt: Path,
    visuals: list[dict[str, Any]],
    output: Path,
    scale: float = INSET_SCALE,
    background: str = BACKGROUND,
) -> Path:
    """Compose the video into the upper left of a pale field, then caption it."""
    box = geometry(scale)
    output.parent.mkdir(parents=True, exist_ok=True)

    command = ["ffmpeg", "-y", "-i", str(video)]
    for visual in visuals:
        command.extend(["-loop", "1", "-i", visual["file"]])

    filters = [
        f"color=c={background}:s={CANVAS[0]}x{CANVAS[1]}:r=30[bg]",
        f"[0:v]scale={box['width']}:{box['height']},setsar=1[inset]",
        f"[bg][inset]overlay={box['x']}:{box['y']}:shortest=1[framed]",
        # Captions are drawn on the composed frame so they stay full size.
        f"[framed]subtitles=filename='{_filter_path(srt)}'"
        f":force_style='{_style(srt, box['caption_margin'])}'[v0]",
    ]
    previous = "v0"
    for index, visual in enumerate(visuals, start=1):
        current = f"v{index}"
        filters.append(
            f"[{index}:v]format=rgba,scale={box['width']}:{box['height']}[card{index}];"
            f"[{previous}][card{index}]overlay={box['x']}:{box['y']}"
            f":enable='between(t,{visual['start']},{visual['end']})'[{current}]"
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
    return output
