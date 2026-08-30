"""Turning a finished script into the film.

Every shot in the script already knows what it is -- a card with a
specification, a photograph with a path, a passage of footage with a start and
an end -- so this does no choosing. That is the point: the choosing happened
while the words were being written, where the writer could see what was
available, rather than days later by somebody reading a description.

Three kinds of shot, each rendered to the same 1080x1920 frame and the same
length as its line, then joined:

    card    drawn here, and it moves: see core/cards.py
    pic     a still, pushed in slowly so it reads as film rather than a slide
    clip    a passage of the source, silent, in the tall frame

The caption is burnt on last, over all of them, so type does not change
between shot kinds.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from core import cards as cards_module
from core import script as script_module
from core import shorts as shorts_module

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "shorts"

W, H, FPS = shorts_module.WIDTH, shorts_module.HEIGHT, shorts_module.FPS
CAPTION_TOP = H - 420
CAPTION_SIZE = 64
ROW_STEP = 92
FONT = "/System/Library/Fonts/PingFang.ttc"


def caption_layer(rows: list[str]) -> Image.Image:
    """The line, drawn once, over whatever the shot is.

    Drawn rather than burnt by ffmpeg's drawtext so that a card and a
    photograph carry identical type -- the first cut of this had the captions
    coming from two places and they did not match.

    The plate behind is not decoration: white on a bright frame is unreadable,
    and these are watched with the sound off, so the words are the whole of
    the information.
    """
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = ImageFont.truetype(FONT, CAPTION_SIZE, index=1)
    widest = max((draw.textlength(row, font=font) for row in rows), default=0)
    high = len(rows) * ROW_STEP
    box = [(W - widest) / 2 - 34, CAPTION_TOP - 26,
           (W + widest) / 2 + 34, CAPTION_TOP + high + 10]
    draw.rounded_rectangle(box, 18, fill=(6, 10, 14, 168))
    for index, row in enumerate(rows):
        draw.text((W / 2, CAPTION_TOP + index * ROW_STEP), row, font=font,
                  fill=(255, 255, 255, 255), anchor="ma")
    return layer


def _still(pic: Path, seconds: float, target: Path, overlay: Path) -> Path:
    """A photograph, held, pushed in slowly, over a blurred enlargement of
    itself so the tall frame is filled rather than letterboxed."""
    graph = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},boxblur=44:3,eq=brightness=-0.16[bg];"
        f"[0:v]{shorts_module._push_filter(seconds)}[fg];"
        f"[bg][fg]overlay=(W-w)/2:{shorts_module.PICTURE_TOP}[under];"
        f"[under][1:v]overlay=0:0,fps={FPS}[v]")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-loop", "1", "-i", str(pic),
         "-i", str(overlay), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-filter_complex", graph, "-map", "[v]", "-map", "2:a",
         "-t", f"{seconds:.2f}", "-c:v", "libx264", "-preset", "medium",
         "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-shortest", str(target), "-y"], check=True)
    return target


def _card(spec: dict[str, Any], seconds: float, target: Path,
          overlay: Path) -> Path:
    """A drawn shot. The frames come from cards.py and go straight into
    ffmpeg; the caption is composited onto each one in Pillow, which is
    cheaper than a second encode and keeps the type identical to the stills."""
    plate = Image.open(overlay).convert("RGBA")
    pipe = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-shortest", str(target), "-y"], stdin=subprocess.PIPE)
    for frame in cards_module.frames(spec, seconds, FPS):
        merged = Image.alpha_composite(frame.convert("RGBA"), plate)
        pipe.stdin.write(merged.convert("RGB").tobytes())
    pipe.stdin.close()
    if pipe.wait() != 0:
        raise RuntimeError("ffmpeg 沒有把卡片畫成影片")
    return target


def build(name: str, target: Path | None = None,
          say: Any = None) -> dict[str, Any]:
    """The whole film, one shot per line."""
    found = script_module.load(name)
    measured = script_module.measure(found)

    # Whose footage each shot is. The script names a file; who broadcast it is
    # recorded against the topic, and the first cut of this film went out
    # without the credit line because that join only existed in the web
    # handler. The channel logo survived, which is why it was not obvious --
    # but the mark we put on our own frame is the one no crop can remove, and
    # it was simply missing.
    from core import topic as topic_module
    pictures: dict[str, dict[str, Any]] = {}
    footage: dict[str, dict[str, Any]] = {}
    try:
        pile = topic_module.load(found.get("topic", ""))
        pictures = {item["file"]: item for item in pile["sources"]["images"]
                    if item.get("file")}
        footage = {item["file"]: item for item in pile["sources"]["videos"]
                   if item.get("file")}
    except (ValueError, FileNotFoundError):
        pass

    for fault in ("unpicked", "undrawn"):
        if measured[fault]:
            raise RuntimeError(
                f"{name} 還有 {len(measured[fault])} 句沒有畫面：" +
                "、".join(item["say"] for item in measured[fault][:3]))

    work = OUT_DIR / f".{name}"
    work.mkdir(parents=True, exist_ok=True)
    target = target or OUT_DIR / f"{name}.mp4"
    pieces = []
    for index, line in enumerate(measured["lines"]):
        seconds = line["seconds"]
        plate = work / f"cap{index:02d}.png"
        caption_layer(script_module.wrap(line["say"])).save(plate)
        piece = work / f"{index:02d}.mp4"
        if not piece.is_file():
            if line.get("clip"):
                who = footage.get(line["clip"]["file"], {}).get("outlet", "")
                shorts_module.clip_cut(
                    ROOT / line["clip"]["file"], line["clip"]["start"],
                    line["clip"]["end"], seconds, piece, overlay=plate,
                    credit=f"畫面來源：{who}" if who else "")
            elif line.get("pic"):
                _still(ROOT / line["pic"], seconds, piece, plate)
            else:
                _card(line["card"], seconds, piece, plate)
        pieces.append(piece)
        if say:
            say(index + 1, len(measured["lines"]), line["say"])

    listing = work / "join.txt"
    listing.write_text("".join(f"file '{piece}'\n" for piece in pieces),
                       encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", "-movflags", "+faststart",
         str(target), "-y"], check=True)
    return {"file": str(target.relative_to(ROOT)),
            "seconds": measured["seconds"], "shots": len(pieces),
            "rights": script_module.rights(found, pictures, measured)}
