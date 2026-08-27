"""The layout as data, so the preview and the burn cannot disagree.

Every visible thing -- the picture, the captions, a corner mark, an explanatory
image -- is an element with a box in 1920x1080 pixels and, optionally, a time
range. One file describes the frame; the browser lays it out with HTML and
ffmpeg burns it. Neither owns the geometry, so what is dragged is what renders.

Boxes are [left, top, right, bottom] in canvas pixels rather than fractions:
percentages read fine in CSS but every ffmpeg filter wants integers, and
rounding twice is how a preview and a render drift apart.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CANVAS = (1920, 1080)
BACKGROUND = "#DCE9F5"
DEFAULT_SECONDS = 5.0       # how long a newly placed image stays

# libass reads margins in script coordinates, not pixels: an SRT defaults to
# PlayResX/Y 384x288, so margins are scaled by 5 and 3.75 on a 1920x1080 frame.
# A pixel value passed straight through lands off-frame and draws nothing, with
# no error raised -- these two constants are the whole reason subtitle boxes
# need converting rather than copying.
SCRIPT_X = CANVAS[0] / 384
SCRIPT_Y = CANVAS[1] / 288


def full_scene(srt_name: str = "subtitles_bilingual.srt") -> dict[str, Any]:
    """Captions over the whole picture, no field beside it. The layout the
    pipeline used before there was anything to put in the margin; kept as a
    scene so there is one renderer rather than one per layout."""
    return {
        "canvas": list(CANVAS),
        "background": "#000000",
        "elements": [
            {"id": "video", "type": "video", "box": [0, 0, *CANVAS]},
            {
                "id": "subtitle",
                "type": "subtitle",
                "srt": srt_name,
                "box": [0, CANVAS[1] - 250, CANVAS[0], CANVAS[1] - 60],
                "font": "PingFang TC",
                "size": 20,
                "colour": "#FFFFFF",
                "outline": "#000000",
                "outline_width": 3,
            },
        ],
    }


def add_badges(
    scene: dict[str, Any],
    images: list[Path],
    duration: float,
    every: float = 60.0,
) -> dict[str, Any]:
    """Show the images in the lower right, one at a time, cycling. A channel
    with four marks gets variety without anyone scheduling anything."""
    if not images or duration <= 0:
        return scene
    canvas = tuple(scene.get("canvas", CANVAS))
    size, margin = 280, 56
    box = [canvas[0] - margin - size, canvas[1] - margin - size,
           canvas[0] - margin, canvas[1] - margin]
    index, start = 0, 0.0
    while start < duration:
        scene["elements"].append({
            "id": f"badge{index + 1}",
            "type": "image",
            "file": str(images[index % len(images)]),
            "box": list(box),
            "from": round(start, 2),
            "to": round(min(start + every, duration), 2),
        })
        index += 1
        start += every
    return scene


def add_cards(scene: dict[str, Any], visuals: list[dict[str, Any]]) -> dict[str, Any]:
    """Information cards the run planned, as ordinary image elements.

    A card carries its own box when it has been trimmed to what it actually
    draws; otherwise it covers the frame, which is how they were made before
    there was a layout to place them in."""
    canvas = list(scene.get("canvas", CANVAS))
    for index, card in enumerate(visuals, start=1):
        scene["elements"].append({
            "id": f"card{index}",
            "type": "image",
            "file": str(card["file"]),
            "box": list(card.get("box") or [0, 0, *canvas]),
            "from": float(card["start"]),
            "to": float(card["end"]),
        })
    return scene


def default_scene(
    srt_name: str = "subtitles_bilingual.srt",
    icon: str | None = None,
) -> dict[str, Any]:
    """The three pieces every video starts with: picture, captions, channel mark.

    The numbers are the ones arrived at by measuring rendered frames: the
    picture at 60% in the upper left, captions in the field beneath it bounded
    to its width, the mark in the opposite corner.
    """
    width, height = 1152, 648
    margin = 48
    elements: list[dict[str, Any]] = [
        {
            "id": "video",
            "type": "video",
            "box": [margin, margin, margin + width, margin + height],
        },
        {
            "id": "subtitle",
            "type": "subtitle",
            "srt": srt_name,
            "box": [margin, 790, margin + width, 930],
            "font": "PingFang TC",
            "size": 20,
            "colour": "#FFFFFF",
            "outline": "#000000",
            "outline_width": 3,
        },
    ]
    if icon:
        elements.append({
            "id": "icon",
            "type": "image",
            "file": icon,
            "box": [1584, 744, 1864, 1024],
        })
    return {"canvas": list(CANVAS), "background": BACKGROUND, "elements": elements}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, scene: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")


def find(scene: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [item for item in scene.get("elements", []) if item.get("type") == kind]


def one(scene: dict[str, Any], kind: str) -> dict[str, Any] | None:
    found = find(scene, kind)
    return found[0] if found else None


def size_of(element: dict[str, Any]) -> tuple[int, int]:
    """Width and height, forced even -- H.264 rejects odd dimensions."""
    left, top, right, bottom = element["box"]
    return max(2, (right - left) // 2 * 2), max(2, (bottom - top) // 2 * 2)


def subtitle_style(element: dict[str, Any], canvas: tuple[int, int] = CANVAS) -> str:
    """An ASS force_style string placing captions inside the element's box.

    The margin positions the *bottom* of the text block, so it is derived from
    where the caption should end. Left and right margins bound the box the text
    centres inside, which is what keeps a long line under the picture instead of
    running out across the frame.
    """
    left, _, right, bottom = element["box"]
    scale_x, scale_y = canvas[0] / 384, canvas[1] / 288
    return ",".join([
        f"FontName={element.get('font', 'PingFang TC')}",
        f"FontSize={element.get('size', 20)}",
        f"PrimaryColour={_ass_colour(element.get('colour', '#FFFFFF'))}",
        f"OutlineColour={_ass_colour(element.get('outline', '#000000'))}",
        "BorderStyle=1",
        f"Outline={element.get('outline_width', 3)}",
        "Shadow=1",
        f"MarginV={int((canvas[1] - bottom) / scale_y)}",
        f"MarginL={int(left / scale_x)}",
        f"MarginR={int((canvas[0] - right) / scale_x)}",
        "Alignment=2",
    ])


def _ass_colour(value: str) -> str:
    """#RRGGBB to ASS &HAABBGGRR: ASS orders the channels backwards."""
    text = value.lstrip("#")
    if len(text) != 6:
        return "&H00FFFFFF"
    red, green, blue = text[0:2], text[2:4], text[4:6]
    return f"&H00{blue}{green}{red}".upper()


def timed(element: dict[str, Any]) -> tuple[float, float] | None:
    """The element's time range, or None when it is present throughout."""
    start, end = element.get("from"), element.get("to")
    if start is None and end is None:
        return None
    return float(start or 0.0), float(end if end is not None else 1e9)
