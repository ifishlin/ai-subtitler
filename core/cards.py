"""The shots we draw ourselves.

Seven tenths of one of these videos is a card. That is not a shortfall to be
made up with more footage -- a card is the only shot that can say "they are
not counting the same thing", which no photograph can -- but it does mean the
cards decide whether the thing is watchable, and for a long time they were
being written as prose in a `show` field and drawn much later, by hand, one
script at a time.

So the vocabulary is small and named. A line says which kind of card it wants
and what goes in it; this draws it. Where a line wants something the
vocabulary has no word for, it gets a title card, which is honest -- a
sentence in large type is a real shot and every channel in this format uses
one -- rather than an approximation of a diagram nobody specified.

    1080x1920, the same frame the footage is placed in.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CARD_DIR = ROOT / "assets" / "cards"

W, H = 1080, 1920
# Where the burnt-in caption starts. Nothing drawn may reach it, or the line
# being spoken sits on top of the picture explaining it.
CAPTION_TOP = H - 420
FONT = "/System/Library/Fonts/PingFang.ttc"

INK = "#0d1b2a"          # the ground: a navy dark enough for white to sit on
GOLD = "#ffd76a"         # what the card is about
DIM = "#9db4c6"          # the note under it
PALE = "#2b4459"         # rules and axes
RED, TEAL, BLUE = "#d1453b", "#1f9d8f", "#4f8ef0"


def face(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT, size, index=1 if bold else 0)


def _base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    card = Image.new("RGB", (W, H), INK)
    return card, ImageDraw.Draw(card)


def _mid(draw: ImageDraw.ImageDraw, y: int, text: str, size: int,
         fill: str = "#fff", bold: bool = True) -> None:
    draw.text((W // 2, y), text, font=face(size, bold), fill=fill, anchor="ma")


def _note(draw: ImageDraw.ImageDraw, text: str) -> None:
    """Where the number came from, small, above the caption.

    On the card rather than only in the description: a figure with no source
    on screen is the thing viewers screenshot and argue about.
    """
    if text:
        _mid(draw, CAPTION_TOP - 78, text, 30, "#6b8299", bold=False)


# Where a card's content begins. The same line the kept picture is placed on
# in core/shorts.py, so a card and a photograph put their subject in the same
# part of the frame and the cut between them does not jump.
TOP = 470


def _heading(draw: ImageDraw.ImageDraw, text: str, y: int = TOP) -> int:
    """The card's own title, wrapped by hand at the width it was written for."""
    rows = [row for row in str(text).split("\n") if row]
    for index, row in enumerate(rows):
        _mid(draw, y + index * 106, row, 76, GOLD)
    return y + len(rows) * 106 + 40


# --- the vocabulary ---------------------------------------------------------

def _title(spec: dict[str, Any]) -> Image.Image:
    """A sentence, large. The fallback, and a real shot in its own right."""
    card, draw = _base()
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    size = 96 if max((len(row) for row in rows), default=0) <= 8 else 76
    top = 760 - (len(rows) - 1) * (size + 24) // 2
    for index, row in enumerate(rows):
        _mid(draw, top + index * (size + 24), row, size, GOLD)
    if spec.get("under"):
        _mid(draw, top + len(rows) * (size + 24) + 30, spec["under"], 46,
             DIM, bold=False)
    _note(draw, spec.get("note", ""))
    return card


def _number(spec: dict[str, Any]) -> Image.Image:
    """One figure, as big as it will go, with what it measures above it."""
    card, draw = _base()
    if spec.get("title"):
        _mid(draw, 560, spec["title"], 64, DIM, bold=False)
    _mid(draw, 700, str(spec.get("value", "")), 220,
         spec.get("colour") or GOLD)
    if spec.get("under"):
        _mid(draw, 960, spec["under"], 54, DIM, bold=False)
    _note(draw, spec.get("note", ""))
    return card


def _bars(spec: dict[str, Any]) -> Image.Image:
    """Two or three quantities, compared. The one shape a card does better
    than any photograph: a photograph of a bill cannot say `twice`."""
    card, draw = _base()
    top = _heading(draw, spec.get("title", ""))
    rows = spec.get("rows") or []
    biggest = max([float(row[1]) for row in rows] or [1])
    widest, left = W - 400, 250
    for index, row in enumerate(rows):
        label, value = row[0], float(row[1])
        colour = row[2] if len(row) > 2 else (RED if index else PALE)
        y = top + 60 + index * 150
        length = max(12, widest * value / biggest)
        draw.text((left - 30, y + 30), str(label), font=face(48), fill=DIM,
                  anchor="ra")
        draw.rounded_rectangle([left, y, left + length, y + 74], 12, fill=colour)
        draw.text((left + length + 24, y + 30),
                  row[3] if len(row) > 3 else f"{value:g}",
                  font=face(52), fill=colour, anchor="la")
    _note(draw, spec.get("note", ""))
    return card


def _split(spec: dict[str, Any]) -> Image.Image:
    """One line forking into two: the shape of `they are not counting the same
    thing`, which is this script's whole argument."""
    card, draw = _base()
    top = _heading(draw, spec.get("title", ""))
    stem, fork = top + 120, top + 420
    draw.line([(W // 2, stem), (W // 2, stem + 90)], fill=PALE, width=9)
    ends = [(240, fork), (W - 240, fork)]
    labels = spec.get("branches") or ["", ""]
    for (x, y), text, colour in zip(ends, labels, (BLUE, RED)):
        draw.line([(W // 2, stem + 90), (x, y - 60)], fill=colour, width=9)
        draw.ellipse([x - 18, y - 78, x + 18, y - 42], fill=colour)
        for row_index, row in enumerate(str(text).split("\n")):
            draw.text((x, y + row_index * 62), row, font=face(50), fill=colour,
                      anchor="ma")
    _note(draw, spec.get("note", ""))
    return card


def _chain(spec: dict[str, Any]) -> Image.Image:
    """Points on one wire. Drawn because the argument is that they share it."""
    card, draw = _base()
    top = _heading(draw, spec.get("title", ""))
    names = spec.get("points") or []
    y = top + 220
    draw.line([(140, y), (W - 140, y)], fill=PALE, width=8)
    for index, name in enumerate(names):
        x = 140 + (W - 280) * (index / max(1, len(names) - 1))
        last = index == len(names) - 1
        draw.ellipse([x - 26, y - 26, x + 26, y + 26],
                     fill=RED if last else BLUE)
        draw.text((x, y + 62), str(name), font=face(46),
                  fill=RED if last else DIM, anchor="ma")
    if spec.get("under"):
        _mid(draw, y + 240, spec["under"], 54, GOLD)
    _note(draw, spec.get("note", ""))
    return card


def _queue(spec: dict[str, Any]) -> Image.Image:
    """A line of people waiting. The last shot: what the film is about."""
    card, draw = _base()
    top = _heading(draw, spec.get("title", ""))
    count = int(spec.get("count") or 9)
    y = top + 300
    for index in range(count):
        x = 150 + (W - 300) * (index / max(1, count - 1))
        fade = 0.35 + 0.65 * (index / max(1, count - 1))
        tone = tuple(round(component * fade) for component in (219, 226, 233))
        draw.ellipse([x - 20, y - 74, x + 20, y - 34], fill=tone)
        draw.rounded_rectangle([x - 26, y - 26, x + 26, y + 70], 18, fill=tone)
    if spec.get("under"):
        _mid(draw, y + 170, spec["under"], 54, GOLD)
    _note(draw, spec.get("note", ""))
    return card


def _stack(spec: dict[str, Any]) -> Image.Image:
    """A list. What the other side says, usually -- three reasons that are not
    the one everybody is shouting about."""
    card, draw = _base()
    top = _heading(draw, spec.get("title", ""))
    for index, item in enumerate(spec.get("items") or []):
        y = top + 90 + index * 130
        draw.rounded_rectangle([200, y, W - 200, y + 100], 16,
                               fill="#16293b", outline=PALE, width=3)
        draw.text((W // 2, y + 24), str(item), font=face(52), fill="#dbe2e9",
                  anchor="ma")
    _note(draw, spec.get("note", ""))
    return card


def _clock(spec: dict[str, Any]) -> Image.Image:
    """A span of years, as a dial. Used where the point is how long the wait
    is -- seven years reads as a number and lands as a circle."""
    card, draw = _base()
    top = _heading(draw, spec.get("title", ""))
    centre, radius = (W // 2, top + 300), 200
    draw.ellipse([centre[0] - radius, centre[1] - radius,
                  centre[0] + radius, centre[1] + radius], outline=PALE, width=10)
    part = float(spec.get("part") or 1)
    end = -90 + 360 * min(1.0, part)
    draw.arc([centre[0] - radius, centre[1] - radius,
              centre[0] + radius, centre[1] + radius], -90, end,
             fill=RED, width=24)
    _mid(draw, centre[1] - 70, str(spec.get("value", "")), 140, GOLD)
    if spec.get("under"):
        _mid(draw, centre[1] + radius + 60, spec["under"], 52, DIM, bold=False)
    _note(draw, spec.get("note", ""))
    return card


KINDS = {"title": _title, "number": _number, "bars": _bars, "split": _split,
         "chain": _chain, "queue": _queue, "stack": _stack, "clock": _clock}


def draw(spec: dict[str, Any]) -> Image.Image:
    """One card. An unknown kind becomes a title card rather than an error:
    a script that names a shape nobody has drawn yet should still render."""
    return KINDS.get(str(spec.get("kind") or "title"), _title)(spec)


def name_for(spec: dict[str, Any]) -> str:
    """A filename that changes when the card does, so an edited card is not
    served from the last render."""
    body = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12] + ".png"


def render(script_name: str, spec: dict[str, Any]) -> str:
    """Draw it if it is not already drawn, and give back the path."""
    here = CARD_DIR / script_name
    here.mkdir(parents=True, exist_ok=True)
    target = here / name_for(spec)
    if not target.is_file():
        draw(spec).save(target)
    return str(target.relative_to(ROOT))
