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
# The caption grows upward from a fixed line rather than downward from one.
# Anchored at the top, a three-row line reached 276 pixels further down than a
# one-row line and landed under YouTube's own bottom furniture; anchored here,
# every line ends in the same place and the room above is the picture's.
from core import rules as rules_module

CAPTION_BOTTOM = rules_module.look("frame.caption_bottom", 1700)
CAPTION_SIZE = rules_module.look("frame.caption_size", 64)
ROW_STEP = rules_module.look("frame.row_step", 92)
FONT = rules_module.look("font", "/System/Library/Fonts/PingFang.ttc")


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
    top = CAPTION_BOTTOM - len(rows) * ROW_STEP
    box = [(W - widest) / 2 - 34, top - 26,
           (W + widest) / 2 + 34, top + len(rows) * ROW_STEP + 10]
    draw.rounded_rectangle(box, 18, fill=(6, 10, 14, 168))
    for index, row in enumerate(rows):
        draw.text((W / 2, top + index * ROW_STEP), row, font=font,
                  fill=(255, 255, 255, 255), anchor="ma")
    return layer


def placed_size(pic: Path) -> tuple[int, int]:
    """How big this photograph sits in the frame: full width, its own shape.

    Rounded to even numbers because H.264 chroma is subsampled and an odd
    dimension is rejected outright.
    """
    with Image.open(pic) as opened:
        wide, high = opened.size
    return W, max(2, round(W * high / max(1, wide)) // 2 * 2)


def _ground_colour(tone: str) -> str:
    """The flat colour behind a photograph, taken from the same palette the
    cards use, so a still and the card before it belong to one film."""
    palette = cards_module.tone_of({"tone": tone})
    red, green, blue = palette.get("bottom", (13, 27, 42))
    return f"0x{red:02x}{green:02x}{blue:02x}"


def _still(pic: Path, seconds: float, target: Path, overlay: Path,
           fit: str = "blur", tone: str = "cool") -> Path:
    """A photograph, held, pushed in slowly, standing in the tall frame.

    A landscape picture in a 9:16 frame always leaves room, and how that room
    is filled is a judgement about the picture rather than a default:

        fill    crop to the frame. The strongest, and it throws away two
                thirds of the width -- right when the subject is upright (a
                pylon, a person, one object), wrong for a wide scene
        ground  the palette colour of this part of the film behind it, so
                stills and cards look like one thing rather than cards with
                photographs pasted on
        blur    a blurred enlargement of the picture itself. Never wrong,
                which is why it was the only option, and dull for the same
                reason: every shot in the film gets the same treatment
    """
    size = placed_size(pic)
    if fit == "fill":
        # Cropping to the whole frame means the push has to happen at frame
        # size too, or the picture is scaled twice and softens.
        front = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                 f"crop={W}:{H},{shorts_module._push_filter(seconds, (W, H))}[fg]")
        graph = f"{front};[fg][1:v]overlay=0:0,fps={FPS}[v]"
    else:
        if fit == "ground":
            back = (f"color=c={_ground_colour(tone)}:s={W}x{H}:"
                    f"d={seconds:.2f}:r={FPS}[bg]")
        else:
            back = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
                    f"crop={W}:{H},boxblur=44:3,eq=brightness=-0.16[bg]")
        graph = (
            f"{back};[0:v]{shorts_module._push_filter(seconds, size)}[fg];"
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

    # The gates, all of them, before a single frame is encoded. A film that
    # takes four minutes to build should not spend them on a script that was
    # never going to be publishable.
    for fault, complaint in (("unpicked", "沒有畫面"), ("undrawn", "沒說卡片怎麼畫"),
                             ("unchecked", "沒有人看過那張圖"),
                             ("uncredited", "引用的畫面沒有出處"),
                             ("shapeless", "結構不對")):
        if measured.get(fault):
            faults = measured[fault]
            why = "、".join(item.get("say") or item.get("why", "")
                            for item in faults[:3])
            raise RuntimeError(f"{name} 有 {len(faults)} 處{complaint}：{why}")
    if not measured["still_enough"]:
        raise RuntimeError(
            f"{name} 只有 {measured['clip_share']}% 的實拍會動，"
            f"至少要一半 —— 無旁白的片子，動態是唯一還在動的東西")

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
                _still(ROOT / line["pic"], seconds, piece, plate,
                       fit=line.get("fit")
                           or rules_module.look("still_fit.default", "blur"),
                       tone=line.get("tone") or "cool")
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
