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
SCRIPT_SCALE_X = CANVAS[0] / 384    # PlayResX defaults to 384 the same way
# Two lines at this size occupy about 195 of the frame's 1080 rows, so the block
# has to end near 900 for its first line to clear the picture at 696. Measured
# from rendered frames: 0.73 left the caption overlapping the video by 79 rows.
CAPTION_BOTTOM = 0.86
BADGE_SIZE = 280           # the corner mark, square
BADGE_MARGIN = 56          # from the right and bottom edges
BADGE_EVERY = 60.0         # seconds one mark stays before the next takes over


def geometry(scale: float = INSET_SCALE) -> dict[str, int]:
    """Where the picture sits, and where captions go beneath it."""
    width = int(CANVAS[0] * scale) // 2 * 2
    height = int(CANVAS[1] * scale) // 2 * 2
    bottom = MARGIN + height
    return {
        "width": width, "height": height, "x": MARGIN, "y": MARGIN,
        # Where the caption block ends, converted to script coordinates.
        "caption_margin": int(CANVAS[1] * (1 - CAPTION_BOTTOM) / SCRIPT_SCALE),
        # Captions centre inside the box left by these margins, so bounding the
        # box to the picture's width centres them under the picture rather than
        # under the whole frame -- otherwise a long line runs out across the
        # field towards the corner mark.
        "caption_left": int(MARGIN / SCRIPT_SCALE_X),
        "caption_right": int((CANVAS[0] - MARGIN - width) / SCRIPT_SCALE_X),
        "column_x": MARGIN + width + MARGIN,
        "column_width": CANVAS[0] - (MARGIN * 2 + width) - MARGIN,
        "band_y": bottom + CAPTION_GAP,
    }


def badge_schedule(
    images: list[Path], duration: float, every: float = BADGE_EVERY
) -> list[dict[str, Any]]:
    """One corner mark per interval, cycling through the images supplied."""
    if not images or duration <= 0:
        return []
    slots = []
    index = 0
    start = 0.0
    while start < duration:
        slots.append({
            "file": str(images[index % len(images)].resolve()),
            "start": round(start, 2),
            "end": round(min(start + every, duration), 2),
        })
        index += 1
        start += every
    return slots


def _style(srt: Path, box: dict[str, int]) -> str:
    """White on a black outline: legible on the pale field and on the picture,
    should a long caption ever reach up into it."""
    # The caption box is the picture's width, not the frame's, so the type has
    # to be small enough that a spoken line still fits on one row: at 26 an
    # English line wrapped to three rows and the first pushed up into the video.
    bilingual = "bilingual" in srt.name
    size = 20 if bilingual else 24
    return (
        f"FontName=PingFang TC,FontSize={size},PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,"
        f"MarginV={box['caption_margin']},MarginL={box['caption_left']},"
        f"MarginR={box['caption_right']},Alignment=2"
    )


def render_inset(
    video: Path,
    srt: Path,
    visuals: list[dict[str, Any]],
    output: Path,
    scale: float = INSET_SCALE,
    background: str = BACKGROUND,
    badges: list[dict[str, Any]] | None = None,
) -> Path:
    """Compose the video into the upper left of a pale field, then caption it."""
    box = geometry(scale)
    output.parent.mkdir(parents=True, exist_ok=True)

    badges = badges or []
    command = ["ffmpeg", "-y", "-i", str(video)]
    for visual in visuals:
        command.extend(["-loop", "1", "-i", visual["file"]])
    for badge in badges:
        command.extend(["-loop", "1", "-i", badge["file"]])

    filters = [
        f"color=c={background}:s={CANVAS[0]}x{CANVAS[1]}:r=30[bg]",
        f"[0:v]scale={box['width']}:{box['height']},setsar=1[inset]",
        f"[bg][inset]overlay={box['x']}:{box['y']}:shortest=1[framed]",
        # Captions are drawn on the composed frame so they stay full size.
        f"[framed]subtitles=filename='{_filter_path(srt)}'"
        f":force_style='{_style(srt, box)}'[v0]",
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

    # The marks sit in the corner of the field, below and right of the picture.
    badge_x = CANVAS[0] - BADGE_SIZE - BADGE_MARGIN
    badge_y = CANVAS[1] - BADGE_SIZE - BADGE_MARGIN
    offset = 1 + len(visuals)
    for index, badge in enumerate(badges):
        current = f"b{index}"
        filters.append(
            f"[{offset + index}:v]format=rgba,scale={BADGE_SIZE}:{BADGE_SIZE}[mark{index}];"
            f"[{previous}][mark{index}]overlay={badge_x}:{badge_y}"
            f":enable='between(t,{badge['start']},{badge['end']})'[{current}]"
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
