"""The shots we draw ourselves.

Seven tenths of the running time is a card. The first set were legible and
dull, and dull is a failure of the same kind as wrong: 29 slides, each fully
formed the instant it appeared, each on the same navy ground, each with its
subject at the same size in the same place, and the bottom third of every
frame empty. Nothing moved. In a video with no narration that is dead air with
words on it.

Five things were wrong, and they are what this module is now organised around:

    they did not move        a number arrives at its value, a bar grows into
                             it, a queue forms one figure at a time
    nothing was ever big     the subject owns the frame -- 400 to 560 pixels,
                             not a 76-pixel heading with a diagram under it
    the ground was flat      a gradient, a faint rule grid, and the card's own
                             word ghosted huge behind it
    29 cards, one palette    the tone turns with the argument: cool while it
                             sets up, light when it turns over, warm when it
                             lands
    every shape was a chart  bars and dials explain; a word across the whole
                             frame, or a ring drawn round one thing, asserts

Drawing is a function of time. `draw(spec, t)` renders the card at a progress
between 0 and 1, so the same code makes the still for the page and the frames
for the video, and a card can never look different in the two places.

    1080x1920, the same frame the footage is placed in.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Iterator

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CARD_DIR = ROOT / "assets" / "cards"

W, H, FPS = 1080, 1920, 30
# Where the burnt-in caption starts. Nothing drawn may reach it, or the line
# being spoken sits on top of the picture explaining it.
CAPTION_TOP = H - 420
# Where a card's subject begins: the line the kept picture is placed on in
# core/shorts.py, so a card and a photograph put their subject in the same
# part of the frame and the cut between them does not jump.
TOP = 470
FONT = "/System/Library/Fonts/PingFang.ttc"


# --- palettes ---------------------------------------------------------------
# The tone turns with the argument rather than with taste. Twenty-nine cards on
# one ground read as one long slide; changing at the turn makes the structure
# visible without a word being spent on it.
TONES = {
    "cool": {"top": (13, 27, 42), "bottom": (18, 41, 61), "ink": "#ffffff",
             "lead": "#ffd76a", "dim": "#9db4c6", "rule": "#22405c",
             "hot": "#ff6b52", "cold": "#4f8ef0", "ghost": (90, 130, 165, 26)},
    "light": {"top": (242, 237, 227), "bottom": (228, 219, 205),
              "ink": "#16232e", "lead": "#b8360a", "dim": "#5f7080",
              "rule": "#cbbfae", "hot": "#b8360a", "cold": "#1f5f8f",
              "ghost": (22, 35, 46, 20)},
    "warm": {"top": (36, 18, 14), "bottom": (54, 26, 19), "ink": "#fff3e8",
             "lead": "#ffb347", "dim": "#c39b86", "rule": "#5c3325",
             "hot": "#ff7a4d", "cold": "#7fb3c9", "ghost": (200, 120, 70, 24)},
}


def tone_of(spec: dict[str, Any]) -> dict[str, Any]:
    return TONES.get(str(spec.get("tone") or "cool"), TONES["cool"])


def face(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size, index=1 if bold else 0)


# --- easing -----------------------------------------------------------------
# Everything arrives; nothing snaps. A card that appears complete is a slide,
# and the eye has nothing to follow from one to the next.

def ease(t: float) -> float:
    """Fast then settling. The shape of something being placed."""
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def stagger(t: float, index: int, count: int, overlap: float = 0.55) -> float:
    """This item's own progress when a group arrives one after another."""
    if count <= 1:
        return ease(t)
    each = 1 / (count - (count - 1) * overlap)
    start = index * each * (1 - overlap)
    return ease((t - start) / each) if t > start else 0.0


# --- the ground -------------------------------------------------------------

def _ground(spec: dict[str, Any]) -> Image.Image:
    """Gradient, rules, and the card's own word ghosted behind it.

    Flat colour is what made the first set read as slides: the frame had
    nothing in it but the diagram, so two thirds of every card was an empty
    field. A photograph never has this problem -- it fills its frame whether
    or not the subject does.
    """
    tone = tone_of(spec)
    card = Image.new("RGB", (W, H), tone["top"])
    draw = ImageDraw.Draw(card)
    top, bottom = tone["top"], tone["bottom"]
    for y in range(0, H, 4):
        part = (y / H) ** 0.85
        draw.rectangle(
            [0, y, W, y + 4],
            fill=tuple(round(a + (b - a) * part) for a, b in zip(top, bottom)))

    rule = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pen = ImageDraw.Draw(rule)
    shade = ImageColorRGB(tone["rule"])
    for x in range(0, W, 90):
        pen.line([(x, 0), (x, H)], fill=(*shade, 16), width=1)
    for y in range(0, H, 90):
        pen.line([(0, y), (W, y)], fill=(*shade, 16), width=1)
    card = Image.alpha_composite(card.convert("RGBA"), rule)

    ghost = str(spec.get("ghost") or "")[:2]
    if ghost:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((W // 2, 620), ghost, font=face(760),
                                   fill=tone["ghost"], anchor="ma")
        card = Image.alpha_composite(card, layer.filter(
            ImageFilter.GaussianBlur(2)))
    return card.convert("RGB")


def ImageColorRGB(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _base(spec: dict[str, Any]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    card = _ground(spec)
    return card, ImageDraw.Draw(card)


def _mid(draw: ImageDraw.ImageDraw, y: float, text: str, size: int,
         fill: str, bold: bool = True) -> None:
    draw.text((W // 2, y), text, font=face(size, bold), fill=fill, anchor="ma")


def _fade(colour: str, part: float, ground: tuple[int, int, int]) -> tuple[int, int, int]:
    """A colour on its way in, mixed towards the ground behind it."""
    part = min(1.0, max(0.0, part))
    return tuple(round(g + (c - g) * part)
                 for c, g in zip(ImageColorRGB(colour), ground))


def _note(draw: ImageDraw.ImageDraw, spec: dict[str, Any], t: float) -> None:
    """Where the number came from, small, above the caption. On the card
    rather than only in the description: a figure with no source on screen is
    the thing viewers screenshot and argue about."""
    if spec.get("note"):
        tone = tone_of(spec)
        _mid(draw, CAPTION_TOP - 78, spec["note"], 30,
             "#" + "".join(f"{v:02x}" for v in
                           _fade(tone["dim"], ease(t * 1.4) * 0.75, tone["bottom"])),
             bold=False)


def _heading(draw: ImageDraw.ImageDraw, spec: dict[str, Any], t: float,
             y: int = TOP, size: int = 68) -> int:
    """The card's own title. Small on purpose now -- it is the caption to the
    subject, not the subject, and treating it as the subject is what kept
    everything at one scale."""
    tone = tone_of(spec)
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    colour = "#" + "".join(f"{v:02x}" for v in
                           _fade(tone["lead"], ease(t * 2), tone["top"]))
    for index, row in enumerate(rows):
        _mid(draw, y + index * (size + 26) - (1 - ease(t * 2)) * 24, row, size,
             colour)
    return y + len(rows) * (size + 26) + 30


# --- the vocabulary ---------------------------------------------------------

def _word(spec: dict[str, Any], t: float) -> Image.Image:
    """One statement, across the whole frame.

    The shape the first set had no word for. Everything there explained
    something; nothing asserted anything. A sentence at 200 pixels is not a
    heading, it is the shot.
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    longest = max((len(row) for row in rows), default=1)
    size = 300 if longest <= 3 else 220 if longest <= 5 else 150 if longest <= 7 else 108
    top = 820 - (len(rows) - 1) * (size + 20) // 2
    for index, row in enumerate(rows):
        part = stagger(t, index, len(rows))
        colour = "#" + "".join(f"{v:02x}" for v in
                               _fade(spec.get("colour") or tone["lead"], part,
                                     tone["top"]))
        _mid(draw, top + index * (size + 20) + (1 - part) * 40, row, size, colour)
    if spec.get("under"):
        _mid(draw, top + len(rows) * (size + 20) + 40, spec["under"], 52,
             tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _title(spec: dict[str, Any], t: float) -> Image.Image:
    """A sentence, large, with room under it. The fallback."""
    return _word({**spec, "ghost": spec.get("ghost", "")}, t)


def _number(spec: dict[str, Any], t: float) -> Image.Image:
    """One figure, counted up to.

    Counting is the whole difference between a chart and a slide: the eye
    follows a number that is still moving, and arrives with it.
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    if spec.get("title"):
        _mid(draw, 520, spec["title"], 58, tone["dim"], bold=False)

    text = str(spec.get("value", ""))
    part = ease(t)
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    if digits and part < 1:
        try:
            shown = text.replace(digits, _counted(digits, part), 1)
        except ValueError:
            shown = text
    else:
        shown = text
    size = 430 if len(text) <= 3 else 320 if len(text) <= 5 else 230
    colour = spec.get("colour") or tone["lead"]
    _mid(draw, 640, shown, size,
         "#" + "".join(f"{v:02x}" for v in _fade(colour, min(1, part * 1.6),
                                                 tone["top"])))
    if spec.get("under"):
        _mid(draw, 640 + size + 40, spec["under"], 54, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _counted(digits: str, part: float) -> str:
    """The number on its way to itself, with the same number of decimals so
    the text does not change width as it counts."""
    value = float(digits)
    places = len(digits.split(".")[1]) if "." in digits else 0
    return f"{value * part:.{places}f}"


def _bars(spec: dict[str, Any], t: float) -> Image.Image:
    """Two or three quantities, growing into place. The one shape a card does
    better than any photograph: a photograph of a bill cannot say `twice`."""
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    rows = spec.get("rows") or []
    biggest = max([float(row[1]) for row in rows] or [1])
    widest, left = W - 420, 260
    for index, row in enumerate(rows):
        label, value = row[0], float(row[1])
        colour = row[2] if len(row) > 2 else (tone["hot"] if index else tone["rule"])
        part = stagger(t, index, len(rows))
        y = top + 80 + index * 190
        length = max(10, widest * (value / biggest) * part)
        draw.text((left - 34, y + 34), str(label), font=face(50),
                  fill=tone["dim"], anchor="ra")
        draw.rounded_rectangle([left, y, left + length, y + 96], 14, fill=colour)
        if part > 0.25:
            draw.text((left + length + 26, y + 30),
                      row[3] if len(row) > 3 else f"{value:g}",
                      font=face(62), fill=colour, anchor="la")
    _note(draw, spec, t)
    return card


def _split(spec: dict[str, Any], t: float) -> Image.Image:
    """One line forking into two, drawn as it forks: the shape of `they are
    not counting the same thing`, which is this script's whole argument."""
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    stem, fork = top + 130, top + 470
    grow = ease(min(1.0, t * 1.5))
    draw.line([(W // 2, stem), (W // 2, stem + 100 * grow)],
              fill=tone["rule"], width=10)
    labels = spec.get("branches") or ["", ""]
    for (x, colour), text in zip(((250, tone["cold"]), (W - 250, tone["hot"])),
                                 labels):
        arm = ease(max(0.0, (t - 0.35) / 0.65))
        end = (W // 2 + (x - W // 2) * arm, stem + 100 + (fork - 80 - stem - 100) * arm)
        draw.line([(W // 2, stem + 100), end], fill=colour, width=11)
        if arm > 0.6:
            draw.ellipse([end[0] - 20, end[1] - 20, end[0] + 20, end[1] + 20],
                         fill=colour)
            for row_index, row in enumerate(str(text).split("\n")):
                draw.text((x, fork + row_index * 72), row, font=face(58),
                          fill=colour, anchor="ma")
    _note(draw, spec, t)
    return card


def _chain(spec: dict[str, Any], t: float) -> Image.Image:
    """Points on one wire, lighting up along it. Drawn because the argument
    is that they share it."""
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    names = spec.get("points") or []
    y = top + 250
    run = ease(min(1.0, t * 1.3))
    draw.line([(150, y), (150 + (W - 300) * run, y)], fill=tone["rule"], width=9)
    for index, name in enumerate(names):
        x = 150 + (W - 300) * (index / max(1, len(names) - 1))
        if 150 + (W - 300) * run < x - 10:
            continue
        last = index == len(names) - 1
        colour = tone["hot"] if last else tone["cold"]
        draw.ellipse([x - 30, y - 30, x + 30, y + 30], fill=colour)
        draw.text((x, y + 74), str(name), font=face(52),
                  fill=colour if last else tone["dim"], anchor="ma")
    if spec.get("under") and t > 0.7:
        _mid(draw, y + 260, spec["under"], 66, tone["lead"])
    _note(draw, spec, t)
    return card


def _queue(spec: dict[str, Any], t: float) -> Image.Image:
    """A line of people, forming one at a time. The last shot: what the film
    is about."""
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    count = int(spec.get("count") or 9)
    y = top + 330
    for index in range(count):
        part = stagger(t, index, count, overlap=0.75)
        if part <= 0.02:
            continue
        x = 150 + (W - 300) * (index / max(1, count - 1))
        weight = 0.3 + 0.7 * (index / max(1, count - 1))
        base = ImageColorRGB(tone["ink"])
        tint = tuple(round(component * weight * part) for component in base)
        lift = (1 - part) * 30
        draw.ellipse([x - 22, y - 82 + lift, x + 22, y - 38 + lift], fill=tint)
        draw.rounded_rectangle([x - 29, y - 30 + lift, x + 29, y + 76 + lift],
                               20, fill=tint)
    if spec.get("under") and t > 0.6:
        _mid(draw, y + 190, spec["under"], 66, tone["lead"])
    _note(draw, spec, t)
    return card


def _stack(spec: dict[str, Any], t: float) -> Image.Image:
    """A list, dropping in. What the other side says, usually -- three reasons
    that are not the one everybody is shouting about."""
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    items = spec.get("items") or []
    for index, item in enumerate(items):
        part = stagger(t, index, len(items))
        if part <= 0.02:
            continue
        y = top + 110 + index * 150 - (1 - part) * 40
        panel = _fade(tone["rule"], 0.55 * part, tone["bottom"])
        draw.rounded_rectangle([200, y, W - 200, y + 118], 18, fill=panel)
        draw.text((W // 2, y + 30), str(item), font=face(58),
                  fill="#" + "".join(f"{v:02x}" for v in
                                     _fade(tone["ink"], part, tone["bottom"])),
                  anchor="ma")
    _note(draw, spec, t)
    return card


def _clock(spec: dict[str, Any], t: float) -> Image.Image:
    """A span of years, as a dial that sweeps. Used where the point is how
    long the wait is -- seven years reads as a number and lands as a circle."""
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    centre, radius = (W // 2, top + 340), 230
    box = [centre[0] - radius, centre[1] - radius,
           centre[0] + radius, centre[1] + radius]
    draw.ellipse(box, outline=tone["rule"], width=12)
    part = float(spec.get("part") or 1) * ease(t)
    if part > 0.005:
        draw.arc(box, -90, -90 + 360 * min(1.0, part), fill=tone["hot"], width=28)
    _mid(draw, centre[1] - 100, str(spec.get("value", "")), 190, tone["lead"])
    if spec.get("under"):
        _mid(draw, centre[1] + radius + 70, spec["under"], 52, tone["dim"],
             bold=False)
    _note(draw, spec, t)
    return card


def _ring(spec: dict[str, Any], t: float) -> Image.Image:
    """A word with a ring drawn round it, by hand, as you would on paper.

    Asserting rather than explaining -- the register the first set was missing
    entirely. The ring is deliberately not a circle: it overshoots and does
    not quite close, because a drawn mark reads as somebody pointing and a
    perfect ellipse reads as a border.
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    if spec.get("title"):
        _mid(draw, 520, spec["title"], 58, tone["dim"], bold=False)
    text = str(spec.get("value", ""))
    size = 260 if len(text) <= 4 else 180
    _mid(draw, 720, text, size, tone["ink"])

    sweep = ease(max(0.0, (t - 0.3) / 0.7)) * 1.12       # overshoots, then stops
    if sweep > 0.01:
        wide, high = min(W - 180, len(text) * size * 0.62 + 130), size + 150
        centre = (W // 2, 720 + size * 0.55)
        points = []
        for step in range(int(120 * min(sweep, 1.12)) + 1):
            angle = math.radians(-110 + step * 3)
            wobble = 1 + 0.035 * math.sin(step * 0.55)
            points.append((centre[0] + wide / 2 * wobble * math.cos(angle),
                           centre[1] + high / 2 * wobble * math.sin(angle)))
        if len(points) > 1:
            draw.line(points, fill=spec.get("colour") or tone["hot"], width=14,
                      joint="curve")
    if spec.get("under"):
        _mid(draw, 720 + size + 190, spec["under"], 54, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _swap(spec: dict[str, Any], t: float) -> Image.Image:
    """Before and after, the second arriving over the first.

    For the lines that correct something the audience already believes, which
    is most of the second third of any of these scripts.
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    was, now = str(spec.get("was", "")), str(spec.get("now", ""))
    faded = _fade(tone["dim"], 1 - 0.55 * ease(max(0.0, (t - 0.4) / 0.6)),
                  tone["top"])
    _mid(draw, top + 90, was, 96,
         "#" + "".join(f"{v:02x}" for v in faded))
    if t > 0.35:
        width = draw.textlength(was, font=face(96))
        strike = ease((t - 0.35) / 0.35)
        draw.line([(W / 2 - width / 2, top + 150),
                   (W / 2 - width / 2 + width * min(1, strike), top + 150)],
                  fill=tone["hot"], width=10)
    arrive = ease(max(0.0, (t - 0.5) / 0.5))
    if arrive > 0.01:
        _mid(draw, top + 300 + (1 - arrive) * 40, now, 130,
             "#" + "".join(f"{v:02x}" for v in
                           _fade(tone["lead"], arrive, tone["top"])))
    _note(draw, spec, t)
    return card


KINDS = {"title": _title, "word": _word, "number": _number, "bars": _bars,
         "split": _split, "chain": _chain, "queue": _queue, "stack": _stack,
         "clock": _clock, "ring": _ring, "swap": _swap}


def draw(spec: dict[str, Any], t: float = 1.0) -> Image.Image:
    """One card at a moment in its own arrival. An unknown kind becomes a word
    card rather than an error: a script naming a shape nobody has drawn yet
    should still render."""
    return KINDS.get(str(spec.get("kind") or "title"), _word)(spec, t)


def frames(spec: dict[str, Any], seconds: float, fps: int = FPS
           ) -> Iterator[Image.Image]:
    """Every frame of this card.

    The arrival takes a fixed share of the shot rather than all of it: a card
    still moving when the line ends never settles, and the eye needs a moment
    on the finished thing before the cut.
    """
    total = max(1, round(seconds * fps))
    arrive = max(1, round(min(0.62, 1.4 / max(seconds, 0.1)) * total))
    for index in range(total):
        yield draw(spec, min(1.0, (index + 1) / arrive))


def name_for(spec: dict[str, Any], suffix: str = ".png") -> str:
    """A filename that changes when the card does, so an edited card is not
    served from the last render."""
    body = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12] + suffix


def render(script_name: str, spec: dict[str, Any]) -> str:
    """The finished card as a still, for the page."""
    here = CARD_DIR / script_name
    here.mkdir(parents=True, exist_ok=True)
    target = here / name_for(spec)
    if not target.is_file():
        draw(spec, 1.0).save(target)
    return str(target.relative_to(ROOT))


def render_clip(script_name: str, spec: dict[str, Any], seconds: float,
                fps: int = FPS) -> str:
    """The card as it plays: silent, exactly as long as the line.

    Frames go straight into ffmpeg's stdin as raw pixels. Writing a numbered
    PNG per frame would be 72 files per card and 2,000 for a script, all of
    them derivable from twelve characters of specification.
    """
    here = CARD_DIR / script_name
    here.mkdir(parents=True, exist_ok=True)
    target = here / name_for({**spec, "_s": round(seconds, 2)}, ".mp4")
    if target.is_file():
        return str(target.relative_to(ROOT))
    pipe = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-shortest", str(target), "-y"], stdin=subprocess.PIPE)
    for frame in frames(spec, seconds, fps):
        pipe.stdin.write(frame.tobytes())
    pipe.stdin.close()
    if pipe.wait() != 0:
        raise RuntimeError("ffmpeg 沒有把卡片畫成影片")
    return str(target.relative_to(ROOT))
