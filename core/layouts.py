"""House styles: the arrangement a video starts from, saved by name.

A scene describes one video -- where its picture sits, where its captions go,
and also which card appears at 3:10. A layout is the part of that which is not
about any particular video: the frame, the picture's box, the caption style,
the channel mark. Everything timed belongs to the video and is left behind.

That single rule is what makes "save this as a house style" honest. You arrange
a frame in the editor until it looks right, save it, and the next run starts
there -- without dragging along the cards you happened to have on screen.

Layouts live in assets/layouts/ as ordinary JSON, one file each, so they are
shared across projects like any other material and can be edited by hand.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import scene as scene_module

ROOT = Path(__file__).resolve().parent.parent
LAYOUT_DIR = ROOT / "assets" / "layouts"
SAFE_NAME = re.compile(r"[\w一-鿿][\w一-鿿 -]{0,47}")

# The two that were hard-coded, offered as files so they can be edited rather
# than only chosen. Written out on first use; after that the file wins.
BUILT_IN = {
    "左上角": {
        "note": "畫面在左上、字幕在畫面正下方、頻道 icon 在右下角，其餘留白",
        "build": lambda: scene_module.default_scene(icon=_channel_icon()),
    },
    "滿版": {
        "note": "畫面填滿整個框，字幕壓在畫面上",
        "build": lambda: scene_module.full_scene(),
    },
}


def _channel_icon() -> str | None:
    """The channel's mark, if there is one to hand. A cut-out is preferred:
    a square photograph in the corner of every frame is a sticker, not a mark."""
    for folder in ("assets/cutouts", "assets/images"):
        here = ROOT / folder
        found = sorted(here.glob("*.png")) if here.is_dir() else []
        if found:
            return f"{folder}/{found[0].name}"
    return None


def is_timed(element: dict[str, Any]) -> bool:
    """Whether this element belongs to one video rather than to the style."""
    return element.get("from") is not None or element.get("to") is not None


def from_scene(scene: dict[str, Any]) -> dict[str, Any]:
    """A layout taken from an arranged frame: everything except the timing."""
    return {
        "canvas": list(scene.get("canvas") or scene_module.CANVAS),
        "background": scene.get("background") or scene_module.BACKGROUND,
        "elements": [dict(element) for element in scene.get("elements", [])
                     if not is_timed(element)],
    }


def to_scene(layout: dict[str, Any], srt_name: str) -> dict[str, Any]:
    """A layout as the starting scene for a run, pointed at its subtitles."""
    scene = {
        "canvas": list(layout.get("canvas") or scene_module.CANVAS),
        "background": layout.get("background") or scene_module.BACKGROUND,
        "elements": [dict(element) for element in layout.get("elements", [])],
    }
    for element in scene["elements"]:
        if element.get("type") == "subtitle":
            element["srt"] = srt_name
    return scene


def _ensure_built_in() -> None:
    LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, spec in BUILT_IN.items():
        path = LAYOUT_DIR / f"{name}.json"
        if path.is_file():
            continue
        layout = from_scene(spec["build"]())
        layout["note"] = spec["note"]
        path.write_text(json.dumps(layout, ensure_ascii=False, indent=2),
                        encoding="utf-8")


def names() -> list[str]:
    _ensure_built_in()
    return sorted(path.stem for path in LAYOUT_DIR.glob("*.json"))


def listing() -> list[dict[str, Any]]:
    """Every layout with enough about it to be chosen from a list."""
    found = []
    for name in names():
        layout = load(name)
        kinds = [element.get("type") for element in layout.get("elements", [])]
        found.append({
            "name": name,
            "note": layout.get("note", ""),
            "elements": len(kinds),
            "has_icon": any(element.get("id") == "icon"
                            for element in layout.get("elements", [])),
            "built_in": name in BUILT_IN,
        })
    return found


def path_for(name: str) -> Path:
    """Where a layout of this name lives. The name becomes a filename, so it is
    checked rather than joined."""
    if not SAFE_NAME.fullmatch(name):
        raise ValueError("版面名稱只能用中英文、數字、底線、減號、空白")
    return LAYOUT_DIR / f"{name}.json"


def load(name: str) -> dict[str, Any]:
    _ensure_built_in()
    path = path_for(name)
    if not path.is_file():
        raise FileNotFoundError(f"找不到版面 {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(name: str, layout: dict[str, Any], note: str = "") -> Path:
    path = path_for(name)
    kept = from_scene(layout)
    if note:
        kept["note"] = note
    elif layout.get("note"):
        kept["note"] = layout["note"]
    LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def scene_for(name: str, srt_name: str) -> dict[str, Any]:
    """The scene a run should start from, by layout name."""
    return to_scene(load(name), srt_name)
