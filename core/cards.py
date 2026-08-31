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

import functools
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Iterator

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
CARD_DIR = ROOT / "assets" / "cards"

from core import rules as rules_module

W = rules_module.look("frame.width", 1080)
H = rules_module.look("frame.height", 1920)
FPS = rules_module.look("frame.fps", 30)
# The caption sits on a fixed bottom line and grows upward from it, so a
# three-row line and a one-row line end in the same place. Nothing drawn may
# reach the tallest case, or the words sit on the picture explaining them.
CAPTION_BOTTOM = rules_module.look("frame.caption_bottom", 1700)
CAPTION_TOP = CAPTION_BOTTOM - rules_module.at("caption.most_rows", 3) * \
    rules_module.look("frame.row_step", 92)
# Where the source line goes. It used to sit just above the caption, in the
# middle of the ghosted word, which read as the big letter being cut off.
# Above the card instead: nothing else is up there.
NOTE_TOP = rules_module.look("frame.note_top", 300)
# Where a card's subject begins: the line the kept picture is placed on in
# core/shorts.py, so a card and a photograph put their subject in the same
# part of the frame and the cut between them does not jump.
TOP = rules_module.look("frame.card_top", 470)
# Nothing drawn may come closer than this to either edge.
MARGIN = rules_module.look("frame.side_margin", 74)
FONT = rules_module.look("font", "/System/Library/Fonts/PingFang.ttc")

# 一種卡，好幾種畫法。宣告在這裡、內容在函式都定義完之後填 ——
# 名字要先存在才填得進去。
WAYS: dict[str, list] = {}


# --- palettes ---------------------------------------------------------------
# The tone turns with the argument rather than with taste. Twenty-nine cards on
# one ground read as one long slide; changing at the turn makes the structure
# visible without a word being spent on it.
def tone_of(spec: dict[str, Any]) -> dict[str, Any]:
    """This card's palette, from assets/theme.json.

    Read every time rather than captured at import, so changing a colour in the
    file changes the next card drawn -- which is the whole reason for it being
    a file. Lists become tuples because Pillow wants a tuple for a colour and
    JSON has no such thing.
    """
    tones = rules_module.look("tones", {})
    found = tones.get(str(spec.get("tone") or "cool")) or tones.get("cool") or {}
    return {key: tuple(value) if isinstance(value, list) else value
            for key, value in found.items() if key != "why"}


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
        ImageDraw.Draw(layer).text((W // 2, 560), ghost, font=face(820),
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
    """置中一行字。**`size` 是上限，不是命令。**

    十幾個地方用固定字級呼叫這裡，而其中任何一個遇到長一點的字就會畫出畫面。
    `chain` 的 `under` 寫「我們有灣，也有湖，現在只差一個海洋」，左右各被切掉
    一個字 —— 而那是這個專案第四次把字畫出去（前三次是字幕、bars 的列名、
    卡片標題）。

    前三次都是在各自的位置補上量測，所以第四次還是發生了。這次補在共用的這一支：
    放得下就照原本的字級，放不下才縮。改一個地方，十幾個呼叫點一起好。
    """
    size = fits([text], size, bold, room=W - 2 * MARGIN)
    draw.text((W // 2, y), text, font=face(size, bold), fill=fill, anchor="ma")


def room_at(x: float, limit: float | None = None) -> int:
    """一段置中在 x 的字，最寬可以多寬。

    界線是**留白**，不是畫布邊緣。`_chain` 本來用 `min(x, W-x)*2` 算，
    那給的是「畫得進畫布」的寬度 —— 最右邊那個點在 x=930，算出來 284px，
    而它實際可以用的只有 152px（右邊界 1006 減 930，再乘二）。
    所以那個標籤縮到 28px 之後仍然壓在邊上，而我看了一眼說「修好了」。

    `limit` 是另一個上限，通常是「不要撞到隔壁那個點」。
    """
    room = min(x - MARGIN, (W - MARGIN) - x) * 2
    return int(max(24, min(room, limit if limit is not None else room)))


def wrap_at(text: str, size: int, room: int, bold: bool = True,
            most_rows: int = 3) -> tuple[int, list[str]]:
    """把一段字折成幾行，並回報該用多大的字級。

    `fits()` 只會縮字級，而它有下限 24px —— 再放不下就回 24，然後畫出界。
    二十五個字擠進 352px 的空間，24px 還要 600px 寬。

    再小的字不是解法，是把「不出界」換成「看不清楚」。長標籤該做的是折行：
    先試最大的字級，折得完就用；折出來超過三行就縮一級再試。

    中文哪裡都能斷，英文盡量斷在空格 —— 不然 `Bloomberg` 會被切成兩半。
    """
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for step in range(size, 23, -4):
        font = face(step, bold)
        rows, now = [], ""
        for char in str(text):
            if ruler.textlength(now + char, font=font) <= room:
                now += char
                continue
            # 這一行滿了。英文的話往回找最近的空格，不要切在字中間。
            cut = now.rfind(" ")
            if cut > len(now) * 0.4:
                rows.append(now[:cut])
                now = now[cut + 1:] + char
            else:
                rows.append(now)
                now = char
        if now:
            rows.append(now)
        if len(rows) <= most_rows:
            return step, rows
    return 24, [str(text)]


def at(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, size: int,
       fill, room: int, bold: bool = True, anchor: str = "ma") -> None:
    """在 x 畫一行字，放不下就縮。`size` 是上限，不是命令。

    `_mid()` 的兄弟：那一支只處理「置中在畫面正中間」，而卡片上還有一堆
    字置中在別的地方 —— 節點的標籤、分岔的兩端。它們各自寫死字級，
    於是各自會爆版。
    """
    draw.text((x, y), text, font=face(fits([text], size, bold, room=room), bold),
              fill=fill, anchor=anchor)


def fits(rows: list[str], want: int, bold: bool = True,
         room: int | None = None) -> int:
    """（快取的入口，真正的計算在 `_fits` 裡。）"""
    return _fits(tuple(rows), want, bold, room)


@functools.lru_cache(maxsize=4096)
def _fits(rows: tuple[str, ...], want: int, bold: bool,
          room: int | None) -> int:
    """同樣的字問同樣的寬度，答案一定一樣，所以只算一次。

    `_mid()` 改成會量寬度之後，這一支變成**每一格畫面都跑一次** —— 一張卡
    七十五格、一支片三十四張卡，壓片時間從兩分半變成十幾分鐘。量測是對的，
    重算七十五次不是。
    """
    """The largest size at or below `want` that leaves a margin on both sides.

    Sizes used to come from a table of character counts -- three characters
    get 300, five get 220 -- which is a guess about width dressed as a rule.
    Five characters at 220 is 1100 pixels on a 1080 frame, so 「沒有這一欄」
    was drawn off both edges. Measuring costs nothing and cannot be wrong
    about the font it is actually using.
    """
    room = room if room is not None else W - 2 * MARGIN
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for size in range(want, 23, -4):
        font = face(size, bold)
        if max((ruler.textlength(row, font=font) for row in rows), default=0) <= room:
            return size
    return 24


def _fade(colour: str, part: float, ground: tuple[int, int, int]) -> tuple[int, int, int]:
    """A colour on its way in, mixed towards the ground behind it."""
    part = min(1.0, max(0.0, part))
    return tuple(round(g + (c - g) * part)
                 for c, g in zip(ImageColorRGB(colour), ground))


def _note(draw: ImageDraw.ImageDraw, spec: dict[str, Any], t: float) -> None:
    """Where the number came from, small, at the top of the frame. On the card
    rather than only in the description: a figure with no source on screen is
    the thing viewers screenshot and argue about."""
    if spec.get("note"):
        tone = tone_of(spec)
        _mid(draw, NOTE_TOP, spec["note"], 30,
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
    size = fits(rows, size)
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
    size = fits(rows, 300)
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


def _bars_column(spec: dict[str, Any], t: float) -> Image.Image:
    """直的柱子，從底下長上來，名字在柱子底下。

    橫條讀的是「誰比較長」，直柱讀的是「誰比較高」—— 後者對「多／少」
    更直覺，尤其只有兩三根的時候。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    rows = spec.get("rows") or []
    if not rows:
        _note(draw, spec, t)
        return card
    biggest = max([float(row[1]) for row in rows] or [1])
    floor, tall = top + 620, 460
    gap = (W - 2 * MARGIN - 40) / len(rows)
    wide = min(gap * 0.62, 190)
    for index, row in enumerate(rows):
        part = stagger(t, index, len(rows))
        x = MARGIN + 20 + gap * (index + 0.5)
        high = tall * (float(row[1]) / biggest) * part
        colour = (row[2] if len(row) > 2 else "") \
            or (tone["hot"] if index == 0 else tone["cold"])
        draw.rounded_rectangle([x - wide / 2, floor - high, x + wide / 2, floor],
                               14, fill=colour)
        # 值在柱子頂上，名字在底下 —— 兩個都置中在柱子上，各自量自己的寬度。
        if part > 0.3:
            label = str(row[3]) if len(row) > 3 else f"{float(row[1]):g}"
            at(draw, x, floor - high - 78, label, 62, colour,
               room=room_at(x, gap * 0.92))
        step, names = wrap_at(str(row[0]), 46, int(min(gap * 0.92,
                                                      room_at(x))), most_rows=2)
        for row_index, name in enumerate(names):
            draw.text((x, floor + 30 + row_index * (step + 6)), name,
                      font=face(step, False), fill=tone["dim"], anchor="ma")
    draw.line([(MARGIN, floor + 6), (W - MARGIN, floor + 6)],
              fill=tone["rule"], width=5)
    _note(draw, spec, t)
    return card


def _bars_dots(spec: dict[str, Any], t: float) -> Image.Image:
    """每一列一排點，一個點一份量。

    數得出來的量比長度更硬：「五十州」畫成五十個點，眼睛會去數。
    量太大的時候一個點代表多個，底下寫明比例。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    rows = spec.get("rows") or []
    if not rows:
        _note(draw, spec, t)
        return card
    biggest = max([float(row[1]) for row in rows] or [1])
    # 一列最多放 25 個點，超過就一個點代表好幾份。
    per_dot = max(1.0, biggest / 25)
    left = MARGIN + 30
    for index, row in enumerate(rows):
        part = stagger(t, index, len(rows))
        y = top + 130 + index * 190
        step, names = wrap_at(str(row[0]), 46, W - 2 * MARGIN - 60, most_rows=1)
        draw.text((left, y), names[0], font=face(step, False),
                  fill=tone["dim"], anchor="la")
        colour = (row[2] if len(row) > 2 else "") \
            or (tone["hot"] if index == 0 else tone["cold"])
        count = int(round(float(row[1]) / per_dot))
        room = W - 2 * MARGIN - 60
        size = min(34, room / max(1, count) - 8)
        for dot in range(count):
            if dot / max(1, count) > part:
                break
            x = left + dot * (size + 8)
            draw.ellipse([x, y + 70, x + size, y + 70 + size], fill=colour)
        label = str(row[3]) if len(row) > 3 else f"{float(row[1]):g}"
        at(draw, W - MARGIN - 4, y, label, 54, colour,
           room=int(W - MARGIN - left - 40), anchor="ra")
    if per_dot > 1:
        _mid(draw, top + 130 + len(rows) * 190 + 20,
             f"一點 = {per_dot:g}", 40, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _bars_split(spec: dict[str, Any], t: float) -> Image.Image:
    """一整條，按比例分成幾段，接在一起。

    橫條比的是「誰大」，這一種比的是「各佔多少」—— 分母是同一條，
    所以它回答的是「怎麼分的」。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    rows = spec.get("rows") or []
    total = sum(float(row[1]) for row in rows) or 1
    left, right = MARGIN + 20, W - MARGIN - 20
    y, high = top + 200, 130
    run = ease(min(1.0, t * 1.3))
    at_x = left
    for index, row in enumerate(rows):
        wide = (right - left) * (float(row[1]) / total) * run
        colour = (row[2] if len(row) > 2 else "") \
            or (tone["hot"] if index == 0 else
                tone["cold"] if index == 1 else tone["rule"])
        draw.rectangle([at_x, y, at_x + wide, y + high], fill=colour)
        at_x += wide + 4
    # 圖例在底下，一行一個 —— 塞進段落裡的話短的那幾段放不下字。
    for index, row in enumerate(rows):
        part = stagger(max(0.0, (t - 0.4) / 0.6), index, len(rows))
        if part <= 0.02:
            continue
        ly = y + high + 60 + index * 86
        colour = (row[2] if len(row) > 2 else "") \
            or (tone["hot"] if index == 0 else
                tone["cold"] if index == 1 else tone["rule"])
        draw.rounded_rectangle([left, ly + 8, left + 44, ly + 52], 8, fill=colour)
        label = str(row[3]) if len(row) > 3 else f"{float(row[1]):g}"
        step, names = wrap_at(f"{row[0]}　{label}", 50,
                              int(right - left - 70), most_rows=1)
        draw.text((left + 70, ly), names[0], font=face(step, False),
                  fill=tone["ink"], anchor="la")
    _note(draw, spec, t)
    return card


def _bars_pair(spec: dict[str, Any], t: float) -> Image.Image:
    """兩根從中線往左右長，像天平。

    只有兩個量的時候最好用：中線在那裡，一眼看得出哪邊重、重多少。
    第三根之後回到左邊起算。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    rows = (spec.get("rows") or [])[:2]
    if not rows:
        _note(draw, spec, t)
        return card
    biggest = max([float(row[1]) for row in rows] or [1])
    mid, y = W // 2, top + 240
    half = (W - 2 * MARGIN) / 2 - 30
    for index, row in enumerate(rows):
        part = stagger(t, index, len(rows))
        wide = half * (float(row[1]) / biggest) * part
        colour = (row[2] if len(row) > 2 else "") \
            or (tone["cold"] if index == 0 else tone["hot"])
        box = ([mid - 8 - wide, y, mid - 8, y + 120] if index == 0
               else [mid + 8, y, mid + 8 + wide, y + 120])
        draw.rounded_rectangle(box, 14, fill=colour)
        # 名字在條子外側，值在裡面 —— 兩個都往中線的反方向排。
        outer = box[0] - 24 if index == 0 else box[2] + 24
        anchor = "ra" if index == 0 else "la"
        room = int(outer - MARGIN) if index == 0 else int(W - MARGIN - outer)
        step, names = wrap_at(str(row[0]), 50, max(80, room), most_rows=2)
        for row_index, name in enumerate(names):
            draw.text((outer, y + 8 + row_index * (step + 6)), name,
                      font=face(step, False), fill=tone["dim"], anchor=anchor)
        if part > 0.4 and wide > 90:
            label = str(row[3]) if len(row) > 3 else f"{float(row[1]):g}"
            spot = box[0] + 20 if index == 0 else box[2] - 20
            at(draw, spot, y + 26, label, 58,
               tone["top"] if isinstance(tone["top"], str) else "#0d1b2a",
               room=int(wide) - 30, anchor="la" if index == 0 else "ra")
    draw.line([(mid, y - 40), (mid, y + 170)], fill=tone["rule"], width=5)
    _note(draw, spec, t)
    return card


def _word_left(spec: dict[str, Any], t: float) -> Image.Image:
    """靠左，一行一行從左邊推進來。

    置中的字是「宣告」，靠左的字是「有人在講」—— 同一句話，兩種語氣。
    左邊留一道豎線當基準，眼睛才知道每一行從哪裡開始。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    size = fits(rows, 230, room=W - MARGIN - 150)
    top = 800 - (len(rows) - 1) * (size + 22) // 2
    rule = ease(min(1.0, t * 1.8))
    draw.line([(120, top - 20), (120, top - 20 + (len(rows) * (size + 22)) * rule)],
              fill=tone["rule"], width=8)
    for index, row in enumerate(rows):
        part = stagger(t, index, len(rows))
        colour = "#" + "".join(f"{v:02x}" for v in
                               _fade(spec.get("colour") or tone["lead"], part,
                                     tone["top"]))
        draw.text((150 - (1 - part) * 50, top + index * (size + 22)), row,
                  font=face(size), fill=colour, anchor="la")
    if spec.get("under"):
        at(draw, 150, top + len(rows) * (size + 22) + 40, spec["under"], 52,
           tone["dim"], room=W - MARGIN - 150, bold=False, anchor="la")
    _note(draw, spec, t)
    return card


def _word_boxed(spec: dict[str, Any], t: float) -> Image.Image:
    """關在一個框裡，框先畫出來，字才進去。

    框把一句話變成一件東西 —— 像貼在牆上的一張告示。用在「這就是規定」那種
    句子上，比整片空白的宣告更硬。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    size = fits(rows, 190, room=W - 2 * MARGIN - 120)
    high = len(rows) * (size + 24) + 90
    top = 830 - high // 2
    grow = ease(min(1.0, t * 1.6))
    # 框從中間往兩邊長開，不是淡入 —— 淡入的東西不會被看成「被放上去」。
    wide = (W - 2 * MARGIN - 40) * grow
    draw.rounded_rectangle([W // 2 - wide / 2, top, W // 2 + wide / 2, top + high],
                           26, outline=tone["rule"], width=7)
    if grow > 0.75:
        for index, row in enumerate(rows):
            part = stagger((t - 0.45) / 0.55, index, len(rows))
            if part <= 0.02:
                continue
            colour = "#" + "".join(f"{v:02x}" for v in
                                   _fade(spec.get("colour") or tone["ink"],
                                         part, tone["top"]))
            _mid(draw, top + 45 + index * (size + 24), row, size, colour)
    if spec.get("under"):
        _mid(draw, top + high + 46, spec["under"], 52, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _word_mark(spec: dict[str, Any], t: float) -> Image.Image:
    """字先在，然後一枝螢光筆從左往右刷過去。

    刷過去的是**動作**：那一刷本身說「注意這一句」，而底線或框只是狀態。
    刷痕比字略高一點、兩端不齊，看起來才像手畫的。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    # 刷痕比字寬 18px，兩邊各一 —— 量字級的時候要把它算進去，
    # 不然字剛好貼齊留白，刷痕就出去了。差 2px，掃邊界抓得到，眼睛抓不到。
    size = fits(rows, 250, room=W - 2 * MARGIN - 40)
    top = 820 - (len(rows) - 1) * (size + 20) // 2
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for index, row in enumerate(rows):
        y = top + index * (size + 20)
        sweep = stagger(max(0.0, (t - 0.25) / 0.75), index, len(rows))
        if sweep > 0.02:
            wide = ruler.textlength(row, font=face(size))
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(layer).rounded_rectangle(
                [W // 2 - wide / 2 - 18,
                 y + size * 0.28,
                 W // 2 - wide / 2 - 18 + (wide + 36) * sweep,
                 y + size * 0.98], 10,
                fill=(*ImageColorRGB(tone["hot"]), 70))
            card = Image.alpha_composite(card.convert("RGBA"), layer).convert("RGB")
            draw = ImageDraw.Draw(card)
        _mid(draw, y, row, size, tone["ink"])
    if spec.get("under"):
        _mid(draw, top + len(rows) * (size + 20) + 40, spec["under"], 52,
             tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _word_quote(spec: dict[str, Any], t: float) -> Image.Image:
    """當成一句引言：巨大的引號在後面，字壓在上面。

    用在「他說」那種句子上。引號是背景不是裝飾 —— 它先到，字後到，
    所以讀的順序是「有人講了一句話」而不是「一句話旁邊有符號」。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    size = fits(rows, 210, room=W - 2 * MARGIN - 80)
    top = 840 - (len(rows) - 1) * (size + 20) // 2
    mark = ease(min(1.0, t * 2.2))
    if mark > 0.02:
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pen = ImageDraw.Draw(layer)
        shade = (*ImageColorRGB(tone["lead"]), int(38 * mark))
        pen.text((120, top - 210), "「", font=face(360), fill=shade, anchor="la")
        pen.text((W - 120, top + len(rows) * (size + 20) - 60), "」",
                 font=face(360), fill=shade, anchor="ra")
        card = Image.alpha_composite(card.convert("RGBA"), layer).convert("RGB")
        draw = ImageDraw.Draw(card)
    for index, row in enumerate(rows):
        part = stagger(max(0.0, (t - 0.2) / 0.8), index, len(rows))
        colour = "#" + "".join(f"{v:02x}" for v in
                               _fade(spec.get("colour") or tone["ink"], part,
                                     tone["top"]))
        _mid(draw, top + index * (size + 20) + (1 - part) * 26, row, size, colour)
    if spec.get("under"):
        _mid(draw, top + len(rows) * (size + 20) + 50, spec["under"], 52,
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
    size = fits([text], 430)
    colour = spec.get("colour") or tone["lead"]
    _mid(draw, 640, shown, size,
         "#" + "".join(f"{v:02x}" for v in _fade(colour, min(1, part * 1.6),
                                                 tone["top"])))
    if spec.get("under"):
        _mid(draw, 640 + size + 40, spec["under"], 54, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _number_dial(spec: dict[str, Any], t: float) -> Image.Image:
    """數字待在一個圓環裡，環跟著數字一起長。

    環把「多少」變成「多滿」—— 同一個數字，多一個「相對於什麼」的暗示。
    沒有刻度、沒有百分比：那會變成圖表，而這張卡要的是一個斷言。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    if spec.get("title"):
        _mid(draw, 500, spec["title"], 58, tone["dim"], bold=False)
    text = str(spec.get("value", ""))
    part = ease(t)
    centre, radius = (W // 2, 860), 300
    box = [centre[0] - radius, centre[1] - radius,
           centre[0] + radius, centre[1] + radius]
    draw.ellipse(box, outline=tone["rule"], width=16)
    if part > 0.02:
        draw.arc(box, -90, -90 + 360 * part,
                 fill=spec.get("colour") or tone["lead"], width=16)
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    shown = text
    if digits and part < 1:
        try:
            shown = text.replace(digits, _counted(digits, part), 1)
        except ValueError:
            pass
    at(draw, centre[0], centre[1] - 110, shown, 250, tone["ink"],
       room=radius * 2 - 90)
    if spec.get("under"):
        _mid(draw, centre[1] + radius + 60, spec["under"], 54, tone["dim"],
             bold=False)
    _note(draw, spec, t)
    return card


def _number_unit(spec: dict[str, Any], t: float) -> Image.Image:
    """數字很大，單位掛在右下角，底下一條線把它托住。

    「230」和「億美元」不是同一個東西 —— 一個是量，一個是尺。分開排，
    眼睛先拿到量，再拿到尺，那是讀數字的自然順序。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    if spec.get("title"):
        _mid(draw, 520, spec["title"], 58, tone["dim"], bold=False)
    text = str(spec.get("value", ""))
    part = ease(t)
    # 前面連續的數字是「量」，後面剩下的是「尺」。
    cut = 0
    while cut < len(text) and (text[cut].isdigit() or text[cut] in ".,:-"):
        cut += 1
    head, tail = (text[:cut], text[cut:]) if cut else (text, "")
    if head and part < 1:
        digits = "".join(ch for ch in head if ch.isdigit() or ch == ".")
        if digits:
            try:
                head = head.replace(digits, _counted(digits, part), 1)
            except ValueError:
                pass
    size = fits([head], 400, room=W - 2 * MARGIN - (200 if tail else 0))
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    wide = ruler.textlength(head, font=face(size))
    left = (W - wide - (140 if tail else 0)) / 2
    colour = "#" + "".join(f"{v:02x}" for v in
                           _fade(spec.get("colour") or tone["lead"],
                                 min(1, part * 1.6), tone["top"]))
    draw.text((left, 680), head, font=face(size), fill=colour, anchor="la")
    if tail:
        at(draw, left + wide + 24, 680 + size * 0.62, tail, 92, tone["dim"],
           room=int(W - MARGIN - (left + wide + 24)), anchor="la")
    rule = ease(max(0.0, (t - 0.4) / 0.6))
    if rule > 0.02:
        draw.line([(left, 680 + size + 34),
                   (left + (wide + (160 if tail else 0)) * rule, 680 + size + 34)],
                  fill=tone["rule"], width=9)
    if spec.get("under"):
        _mid(draw, 680 + size + 90, spec["under"], 54, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _number_stamp(spec: dict[str, Any], t: float) -> Image.Image:
    """數字蓋在一個方塊上，像一枚印章落下。

    落下比淡入有份量：這一張用在「已經定了」那種數字上 —— 判決、日期、
    生效的時刻。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    if spec.get("title"):
        _mid(draw, 500, spec["title"], 58, tone["dim"], bold=False)
    text = str(spec.get("value", ""))
    drop = ease(min(1.0, t * 1.5))
    size = fits([text], 320, room=W - 2 * MARGIN - 160)
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    wide = min(W - 2 * MARGIN, ruler.textlength(text, font=face(size)) + 150)
    high = size + 130
    # 從上面掉下來，最後 8% 過衝一點再回位 —— 那一下就是「蓋」。
    over = math.sin(min(1.0, t * 1.5) * math.pi) * 14
    top = 700 - (1 - drop) * 90 + over
    draw.rounded_rectangle([W // 2 - wide / 2, top, W // 2 + wide / 2, top + high],
                           22, fill=spec.get("colour") or tone["hot"])
    if drop > 0.35:
        at(draw, W // 2, top + 52, text, size,
           tone["top"] if isinstance(tone["top"], str) else "#0d1b2a",
           room=int(wide) - 90)
    if spec.get("under"):
        _mid(draw, top + high + 60, spec["under"], 54, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _number_ghost(spec: dict[str, Any], t: float) -> Image.Image:
    """數字巨大到出血，只露出中間那一段。

    大到裝不下，本身就是那個數字的意思。用在「大到不像話」的量上。
    上下各切掉一點，讀得出來，但看得出它裝不下。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    if spec.get("title"):
        _mid(draw, 470, spec["title"], 58, tone["dim"], bold=False)
    text = str(spec.get("value", ""))
    part = ease(t)
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    shown = text
    if digits and part < 1:
        try:
            shown = text.replace(digits, _counted(digits, part), 1)
        except ValueError:
            pass
    # 寬度照樣量 —— 出血是上下的事，左右出去就只是切掉字。
    size = fits([shown], 560, room=W - 2 * MARGIN)
    colour = "#" + "".join(f"{v:02x}" for v in
                           _fade(spec.get("colour") or tone["lead"],
                                 min(1, part * 1.6), tone["top"]))
    _mid(draw, 700, shown, size, colour)
    if spec.get("under"):
        _mid(draw, 700 + size + 30, spec["under"], 54, tone["dim"], bold=False)
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
        # 空字串也要退回預設。`card_wrong` 那道門明寫「空的不算錯」，於是
        # `["名稱", 50, "", "標籤"]` 過得了門，然後 Pillow 說
        # `unknown color specifier: ''`。跟顏色寫成 `ok` 那次是同一個形狀，
        # 只差在那次是有值但不合法，這次是「有欄位但空的」。
        colour = (row[2] if len(row) > 2 else "") \
            or (tone["hot"] if index else tone["rule"])
        part = stagger(t, index, len(rows))
        y = top + 80 + index * 190
        length = max(10, widest * (value / biggest) * part)
        # Beside the bar when it fits, above it when it does not. Measuring
        # alone is not enough here: the gutter is 152 pixels and a fourteen
        # character label cannot fit at any size worth reading, so shrinking
        # only produced smaller clipped text. 「柏林、不來梅、下薩克森、北威州」
        # reached the frame as 「森、北威州」.
        #
        # Third time words have been drawn off the frame in this project. The
        # first two were solved by measuring; this one needed the layout to
        # give way, which is the thing measuring is for -- it tells you when.
        name = str(label)
        room = left - 34 - MARGIN
        size = fits([name], 50, bold=False, room=room)
        if size >= 34:
            draw.text((left - 34, y + 34), name, font=face(size, False),
                      fill=tone["dim"], anchor="ra")
        else:
            draw.text((left, y - 52), name,
                      font=face(fits([name], 44, bold=False,
                                     room=W - left - MARGIN), False),
                      fill=tone["dim"], anchor="la")
        draw.rounded_rectangle([left, y, left + length, y + 96], 14, fill=colour)
        if part > 0.25:
            label = row[3] if len(row) > 3 else f"{value:g}"
            font = face(62)
            # Outside the bar if it fits, inside if it does not. The longest
            # bar is the one whose label has least room left, so a label drawn
            # blindly to the right runs off exactly where the number matters
            # most -- 算一百年 was reaching the frame as 算一.
            outside = left + length + 26
            if outside + draw.textlength(label, font=font) <= W - MARGIN:
                draw.text((outside, y + 30), label, font=font, fill=colour,
                          anchor="la")
            else:
                # 塞進條子裡靠右。這裡本來不量左邊 —— 而一根短條子配一個
                # 長標籤，往左長出去的正是畫面外：值 3 的那根從 x=280 起算，
                # 十個字 62px 要 620px，落在 -340。
                # 外面放不下的時候，裡面的空間只會更小，所以一定要量。
                spot = left + length - 24
                at(draw, spot, y + 30, label, 62,
                   tone["top"] if isinstance(tone["top"], str) else "#0d1b2a",
                   room=int(spot - MARGIN), anchor="ra")
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
            # 兩端的標籤置中在自己那條臂上，而它們只有到畫面中線的一半
            # 空間 —— 再寬就跟對面那個撞在一起。
            room = room_at(x, abs(x - W // 2) * 2 - 40)
            # 文案自己寫的換行優先，剩下的長度不夠就再折。
            step, rows = 58, []
            for part in str(text).split("\n"):
                step, more = wrap_at(part, min(step, 58), room)
                rows += more
            for row_index, row in enumerate(rows):
                draw.text((x, fork + row_index * (step + 14)), row,
                          font=face(step), fill=colour, anchor="ma")
    _note(draw, spec, t)
    return card


def _split_scale(spec: dict[str, Any], t: float) -> Image.Image:
    """一根橫桿掛在中間，兩端各吊一個。

    分岔說的是「分成兩條路」，天平說的是「兩邊在比」—— 用在兩種說法互相
    對立的時候，而不是兩件事各自發展。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    labels = (spec.get("branches") or ["", ""])[:2]
    mid, beam = W // 2, top + 220
    tilt = math.radians(7 * ease(max(0.0, (t - 0.4) / 0.6)))
    half = (W - 2 * MARGIN) / 2 - 40
    # 桿子傾斜，左低右高 —— 傾斜本身就是「不對等」。
    left = (mid - half * math.cos(tilt), beam + half * math.sin(tilt))
    right = (mid + half * math.cos(tilt), beam - half * math.sin(tilt))
    draw.line([(mid, beam - 90), (mid, beam)], fill=tone["rule"], width=8)
    draw.line([left, right], fill=tone["rule"], width=10)
    for (spot, colour), text in zip(((left, tone["cold"]), (right, tone["hot"])),
                                    labels):
        draw.line([spot, (spot[0], spot[1] + 70)], fill=colour, width=6)
        draw.ellipse([spot[0] - 16, spot[1] - 16, spot[0] + 16, spot[1] + 16],
                     fill=colour)
        room = room_at(spot[0], half * 0.95)
        step, rows = wrap_at(str(text).replace("\n", ""), 56, room)
        for index, row in enumerate(rows):
            draw.text((spot[0], spot[1] + 100 + index * (step + 10)), row,
                      font=face(step), fill=colour, anchor="ma")
    _note(draw, spec, t)
    return card


def _split_two(spec: dict[str, Any], t: float) -> Image.Image:
    """畫面從中間切成兩半，一邊一個。

    最直接的對照：沒有線、沒有節點，就是兩塊並排。用在「你在美國看到的」
    對「你在加拿大看到的」那種——兩邊平等，沒有誰分岔出誰。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    labels = (spec.get("branches") or ["", ""])[:2]
    y, high = top + 130, 470
    half = (W - 2 * MARGIN - 30) / 2
    for index, text in enumerate(labels):
        part = stagger(t, index, len(labels))
        if part <= 0.02:
            continue
        x0 = MARGIN + index * (half + 30)
        colour = tone["cold"] if index == 0 else tone["hot"]
        draw.rounded_rectangle([x0, y, x0 + half, y + high * part], 20,
                               outline=colour, width=6)
        if part > 0.6:
            room = int(half - 60)
            step, rows = wrap_at(str(text).replace("\n", ""), 66, room)
            start = y + high / 2 - len(rows) * (step + 12) / 2
            for row_index, row in enumerate(rows):
                draw.text((x0 + half / 2, start + row_index * (step + 12)), row,
                          font=face(step), fill=colour, anchor="ma")
    _note(draw, spec, t)
    return card


def _split_venn(spec: dict[str, Any], t: float) -> Image.Image:
    """兩個圓，疊在一起。

    分岔和並排都在說「兩個不同」，這一種說的是「有一塊是共通的」——
    用在「兩邊其實在講同一件事的不同面」。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    labels = (spec.get("branches") or ["", ""])[:2]
    radius = 210
    centre_y = top + 300
    grow = ease(min(1.0, t * 1.4))
    for index, text in enumerate(labels):
        colour = tone["cold"] if index == 0 else tone["hot"]
        cx = W // 2 + (-1 if index == 0 else 1) * 130 * grow
        draw.ellipse([cx - radius, centre_y - radius,
                      cx + radius, centre_y + radius],
                     outline=colour, width=8)
        # 字放在圓的外側，不放在圓裡 —— 兩圓相疊的地方寫字會兩層疊在一起。
        spot = cx + (-1 if index == 0 else 1) * (radius - 30)
        room = room_at(spot, 420)
        step, rows = wrap_at(str(text).replace("\n", ""), 52, room)
        for row_index, row in enumerate(rows):
            draw.text((spot, centre_y + radius + 40 + row_index * (step + 8)),
                      row, font=face(step), fill=colour, anchor="ma")
    _note(draw, spec, t)
    return card


def _split_road(spec: dict[str, Any], t: float) -> Image.Image:
    """一條路走到底，分成兩條往上岔開。

    原本那個分岔是往下開，這一種往上 —— 讀起來是「接下來會怎樣」，
    而不是「它拆成什麼」。用在講未來的兩種可能。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    labels = (spec.get("branches") or ["", ""])[:2]
    foot, fork = top + 620, top + 300
    grow = ease(min(1.0, t * 1.5))
    draw.line([(W // 2, foot), (W // 2, foot - (foot - fork) * grow)],
              fill=tone["rule"], width=12)
    arm = ease(max(0.0, (t - 0.4) / 0.6))
    for index, text in enumerate(labels):
        colour = tone["cold"] if index == 0 else tone["hot"]
        end_x = W // 2 + (-1 if index == 0 else 1) * 290 * arm
        end_y = fork - 150 * arm
        draw.line([(W // 2, fork), (end_x, end_y)], fill=colour, width=10)
        if arm > 0.6:
            spot = W // 2 + (-1 if index == 0 else 1) * 290
            room = room_at(spot, 420)
            step, rows = wrap_at(str(text).replace("\n", ""), 54, room)
            for row_index, row in enumerate(rows):
                draw.text((spot, end_y - 40 - (len(rows) - row_index) * (step + 8)),
                          row, font=face(step), fill=colour, anchor="ma")
    _note(draw, spec, t)
    return card


def _swap_slide(spec: dict[str, Any], t: float) -> Image.Image:
    """舊的往左滑出去，新的從右邊滑進來，同一條線上。

    劃掉是「這個錯了」，滑走是「這個過去了」—— 用在名字、政策、版本更迭，
    那種沒有對錯只有先後的替換。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    was, now = str(spec.get("was", "")), str(spec.get("now", ""))
    y = top + 190
    room = W - 2 * MARGIN
    go = ease(max(0.0, (t - 0.3) / 0.7))
    # 舊的滑走：一路淡出，滑到左邊界之前就消失，不會撞到邊。
    if go < 0.98:
        size = fits([was], 120, room=room)
        shade = _fade(tone["dim"], max(0.0, 1 - go * 1.3), tone["top"])
        draw.text((W // 2 - go * (W // 2 - MARGIN) * 0.7, y), was,
                  font=face(size), fill="#" + "".join(f"{v:02x}" for v in shade),
                  anchor="ma")
    if go > 0.25:
        part = ease((go - 0.25) / 0.75)
        size = fits([now], 150, room=room)
        at(draw, W // 2 + (1 - part) * (W // 2 - MARGIN) * 0.7, y + 190, now,
           size, "#" + "".join(f"{v:02x}" for v in
                               _fade(tone["lead"], part, tone["top"])),
           room=room)
    # 一條軌道，說明那兩個在同一條線上。
    draw.line([(MARGIN, y + 150), (W - MARGIN, y + 150)],
              fill=tone["rule"], width=5)
    if spec.get("under"):
        _mid(draw, y + 430, spec["under"], 52, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _swap_stack(spec: dict[str, Any], t: float) -> Image.Image:
    """新的疊在舊的上面，蓋住它。

    上下疊而不是左右換：**舊的還在下面**。用在「official 版本改了，
    但大家還是用舊的」那種句子。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    was, now = str(spec.get("was", "")), str(spec.get("now", ""))
    # 兩張面板一張右移 22、一張左移 18，所以可用寬度要各讓出兩倍的偏移量 ——
    # 只扣邊界不扣偏移的話，右邊那張剛好壓在留白上。
    room = W - 2 * MARGIN - 120 - 44
    was_size = fits([was], 110, room=room)
    now_size = fits([now], 140, room=room)
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    y = top + 150
    # 底下那張：灰的，稍微歪一點，看得出是被蓋住的那個。
    wide = ruler.textlength(was, font=face(was_size)) + 90
    draw.rounded_rectangle([W // 2 - wide / 2 + 22, y + 26,
                            W // 2 + wide / 2 + 22, y + was_size + 86], 18,
                           fill=_fade(tone["rule"], 0.5, tone["bottom"]))
    at(draw, W // 2 + 22, y + 52, was, was_size,
       "#" + "".join(f"{v:02x}" for v in _fade(tone["dim"], 0.7, tone["bottom"])),
       room=room)
    drop = ease(max(0.0, (t - 0.35) / 0.65))
    if drop > 0.02:
        wide = ruler.textlength(now, font=face(now_size)) + 110
        oy = y - 40 - (1 - drop) * 70
        draw.rounded_rectangle([W // 2 - wide / 2 - 18, oy,
                                W // 2 + wide / 2 - 18, oy + now_size + 76], 20,
                               fill=spec.get("colour") or tone["hot"])
        if drop > 0.4:
            at(draw, W // 2 - 18, oy + 40, now, now_size,
               tone["top"] if isinstance(tone["top"], str) else "#0d1b2a",
               room=room)
    if spec.get("under"):
        _mid(draw, y + 330, spec["under"], 52, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _swap_arrow(spec: dict[str, Any], t: float) -> Image.Image:
    """舊的在左、新的在右，中間一支箭。

    最直白的一種：A → B。用在需要一眼看懂、不需要態度的替換上。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    was, now = str(spec.get("was", "")), str(spec.get("now", ""))
    y = top + 220
    # 左右各一半，中間留 150 給箭。
    half = (W - 2 * MARGIN - 150) / 2
    # 兩邊各只有一半的寬度，長標籤縮字級縮不下去（fits 的下限是 24），
    # 所以折行。
    shade = "#" + "".join(f"{v:02x}" for v in _fade(tone["dim"], 1, tone["top"]))
    step, rows = wrap_at(was, 88, int(half))
    for i, row in enumerate(rows):
        draw.text((MARGIN + half / 2, y + i * (step + 10)), row,
                  font=face(step), fill=shade, anchor="ma")
    fly = ease(max(0.0, (t - 0.3) / 0.4))
    if fly > 0.02:
        x0, x1 = W // 2 - 62, W // 2 - 62 + 124 * fly
        draw.line([(x0, y + 52), (x1, y + 52)], fill=tone["hot"], width=10)
        if fly > 0.8:
            draw.polygon([(x1 + 30, y + 52), (x1 - 4, y + 30), (x1 - 4, y + 74)],
                         fill=tone["hot"])
    land = ease(max(0.0, (t - 0.55) / 0.45))
    if land > 0.02:
        lit = "#" + "".join(f"{v:02x}" for v in
                            _fade(spec.get("colour") or tone["lead"], land,
                                  tone["top"]))
        step, rows = wrap_at(now, 104, int(half))
        for i, row in enumerate(rows):
            draw.text((W - MARGIN - half / 2,
                       y - (1 - land) * 20 + i * (step + 10)), row,
                      font=face(step), fill=lit, anchor="ma")
    if spec.get("under"):
        _mid(draw, y + 260, spec["under"], 52, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _swap_tear(spec: dict[str, Any], t: float) -> Image.Image:
    """舊的被一道裂縫從中間撕開，新的在下面露出來。

    最用力的一種。用在「這件事被推翻了」而不是「這件事更新了」。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    was, now = str(spec.get("was", "")), str(spec.get("now", ""))
    room = W - 2 * MARGIN
    y = top + 170
    at(draw, W // 2, y, was, 120,
       "#" + "".join(f"{v:02x}" for v in
                     _fade(tone["dim"], max(0.25, 1 - ease(t) * 0.8),
                           tone["top"])), room=room)
    tear = ease(max(0.0, (t - 0.3) / 0.5))
    if tear > 0.02:
        # 一條參差的裂縫，從中間往兩邊裂開。直線是切割，參差才是撕。
        mid, points = y + 78, []
        for step in range(0, 41):
            part = step / 40
            # 線寬 7，兩端各長出 3.5 —— 拉到留白邊上就會超出去。
            x = W // 2 + (part - 0.5) * (W - 2 * MARGIN - 16) \
                * min(1.0, tear * 1.2)
            points.append((x, mid + math.sin(step * 1.7) * 9))
        draw.line(points, fill=tone["hot"], width=7, joint="curve")
    show = ease(max(0.0, (t - 0.5) / 0.5))
    if show > 0.02:
        at(draw, W // 2, y + 220 + (1 - show) * 30, now, 150,
           "#" + "".join(f"{v:02x}" for v in
                         _fade(spec.get("colour") or tone["lead"], show,
                               tone["top"])), room=room)
    if spec.get("under"):
        _mid(draw, y + 420, spec["under"], 52, tone["dim"], bold=False)
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
    # 每個名字置中在自己那個點上，所以它左右各只有「到畫面邊」那麼多空間 ——
    # 頭尾兩個點離邊 150px，於是一個六個字的標籤會兩邊各被切掉一個字。
    # 這是這個專案第四次把字畫出畫面（前三次是字幕、bars 的列名、卡片標題），
    # 而每一次的答案都一樣：**量寬度決定字級，不要用固定值**。
    for index, name in enumerate(names):
        x = 150 + (W - 300) * (index / max(1, len(names) - 1))
        if 150 + (W - 300) * run < x - 10:
            continue
        last = index == len(names) - 1
        colour = tone["hot"] if last else tone["cold"]
        draw.ellipse([x - 30, y - 30, x + 30, y + 30], fill=colour)
        # 頭尾兩個靠邊對齊，中間的置中。
        #
        # 全部置中在自己那個點上的話，頭尾兩個各只有 152px 可用（點在
        # x=930，右邊界 1006），六個字得縮到 24px 才進得去 —— 而那已經
        # 小到看不清楚，等於為了不出界把字犧牲掉。
        # 靠邊對齊之後，最後那個標籤從 1006 往左長，空間變成到隔壁點的中間，
        # 四倍有餘。
        gap = (W - 300) / max(1, len(names) - 1)
        if index == 0:
            spot, anchor, room = MARGIN, "la", int(gap * 0.9)
        elif last:
            spot, anchor, room = W - MARGIN, "ra", int(gap * 0.9)
        else:
            spot, anchor, room = x, "ma", room_at(x, gap * 0.9)
        at(draw, spot, y + 74, str(name), 52,
           colour if last else tone["dim"], room, anchor=anchor)
    if spec.get("under") and t > 0.7:
        _mid(draw, y + 260, spec["under"],
             fits([str(spec["under"])], 66, room=W - 2 * MARGIN), tone["lead"])
    _note(draw, spec, t)
    return card


def _queue_grid(spec: dict[str, Any], t: float) -> Image.Image:
    """排成方陣，一個一個亮起來。

    一排的隊伍講「很多人在等」；方陣講「總共有這麼多」—— 數得出來，
    而且大量的時候一排放不下。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    count = max(1, int(spec.get("count") or 9))
    across = min(10, max(3, math.ceil(math.sqrt(count))))
    down = math.ceil(count / across)
    cell = min(96, (W - 2 * MARGIN) / across, 460 / max(1, down))
    left = W / 2 - across * cell / 2
    y0 = top + 140
    for index in range(count):
        part = stagger(t, index, count, overlap=0.85)
        if part <= 0.02:
            continue
        col, row = index % across, index // across
        x = left + col * cell + cell / 2
        y = y0 + row * cell
        base = ImageColorRGB(spec.get("colour") or tone["ink"])
        tint = tuple(round(one * (0.35 + 0.65 * part)) for one in base)
        r = cell * 0.19
        draw.ellipse([x - r, y, x + r, y + 2 * r], fill=tint)
        draw.rounded_rectangle([x - r * 1.25, y + 2 * r + 6,
                                x + r * 1.25, y + cell * 0.82], r, fill=tint)
    if spec.get("under"):
        _mid(draw, y0 + down * cell + 70, spec["under"], 60, tone["lead"])
    _note(draw, spec, t)
    return card


def _queue_pile(spec: dict[str, Any], t: float) -> Image.Image:
    """疊成一落，一個一個掉上去。

    隊伍是「在等」，一落是「堆著」—— 用在案件、申請、未處理的東西上。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    count = max(1, int(spec.get("count") or 9))
    floor = top + 620
    high = min(46, 460 / count)
    wide = W - 2 * MARGIN - 160
    for index in range(count):
        part = stagger(t, index, count, overlap=0.8)
        if part <= 0.02:
            continue
        y = floor - index * (high + 5) - (1 - part) * 70
        # 每一層左右錯開一點，像疊歪的紙 —— 對齊的話會讀成長條圖。
        shift = math.sin(index * 2.1) * 26
        colour = _fade(spec.get("colour") or tone["cold"],
                       0.5 + 0.5 * part, tone["bottom"])
        draw.rounded_rectangle([MARGIN + 80 + shift, y - high,
                                MARGIN + 80 + shift + wide, y], 8, fill=colour)
    if spec.get("under"):
        _mid(draw, floor + 70, spec["under"], 60, tone["lead"])
    _note(draw, spec, t)
    return card


def _queue_bar(spec: dict[str, Any], t: float) -> Image.Image:
    """一條長格子，一格一格填滿，數字在旁邊跑。

    人形講「誰」，格子講「多少」—— 用在數量本身就是重點的時候。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    count = max(1, int(spec.get("count") or 9))
    y = top + 300
    cell = min(70, (W - 2 * MARGIN) / count - 6)
    left = W / 2 - (cell + 6) * count / 2
    done = 0
    for index in range(count):
        part = stagger(t, index, count, overlap=0.86)
        x = left + index * (cell + 6)
        draw.rounded_rectangle([x, y, x + cell, y + cell], 10,
                               outline=tone["rule"], width=4)
        if part > 0.3:
            done += 1
            draw.rounded_rectangle([x + 6, y + 6, x + cell - 6, y + cell - 6], 7,
                                   fill=spec.get("colour") or tone["hot"])
    _mid(draw, y + cell + 70, str(done), 200, tone["lead"])
    if spec.get("under"):
        _mid(draw, y + cell + 300, spec["under"], 56, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _queue_crowd(spec: dict[str, Any], t: float) -> Image.Image:
    """散開的一群，深淺不一，有遠有近。

    整齊的隊伍是被安排的；散開的一群是自己聚過來的。用在「很多人都這樣」
    而不是「排隊等著」。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    count = max(1, int(spec.get("count") or 9))
    y0, span = top + 180, 430
    base = ImageColorRGB(spec.get("colour") or tone["ink"])
    for index in range(count):
        part = stagger(t, index, count, overlap=0.88)
        if part <= 0.02:
            continue
        # 位置用固定的三角函數散開 —— 亂數會讓同一張卡每次長得不一樣。
        across = (math.sin(index * 2.399) + 1) / 2
        down = (math.sin(index * 3.883) + 1) / 2
        near = 0.55 + 0.45 * down
        x = MARGIN + 60 + across * (W - 2 * MARGIN - 120)
        y = y0 + down * span
        r = 20 * near
        tint = tuple(round(one * (0.3 + 0.7 * near) * part) for one in base)
        draw.ellipse([x - r, y, x + r, y + 2 * r], fill=tint)
        draw.rounded_rectangle([x - r * 1.3, y + 2 * r + 4,
                                x + r * 1.3, y + 5.4 * r], r, fill=tint)
    if spec.get("under"):
        _mid(draw, y0 + span + 190, spec["under"], 60, tone["lead"])
    _note(draw, spec, t)
    return card


def _clock_bar(spec: dict[str, Any], t: float) -> Image.Image:
    """一條時間軸，填到某個位置停下來。

    圓圈把「多久」畫成一個週期；直線畫成一段路 —— 用在「還要多久」而不是
    「佔了多少」。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    part = float(spec.get("part") or 1) * ease(t)
    y = top + 300
    left, right = MARGIN + 30, W - MARGIN - 30
    draw.rounded_rectangle([left, y, right, y + 78], 14, fill=tone["rule"])
    draw.rounded_rectangle([left, y, left + (right - left) * min(1.0, part),
                            y + 78], 14, fill=spec.get("colour") or tone["hot"])
    at(draw, W // 2, y + 150, str(spec.get("value", "")), 210, tone["lead"],
       room=W - 2 * MARGIN)
    if spec.get("under"):
        _mid(draw, y + 420, spec["under"], 52, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _clock_sand(spec: dict[str, Any], t: float) -> Image.Image:
    """沙漏：上面的沙掉到下面。

    用在「時間在跑」的句子上 —— 圓圈是靜的，沙漏是動的，即使兩個都在
    講同一段長度。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    part = float(spec.get("part") or 1) * ease(t)
    cx, cy, half = W // 2, top + 320, 190
    colour = spec.get("colour") or tone["hot"]
    draw.line([(cx - half, cy - half), (cx + half, cy - half)],
              fill=tone["rule"], width=10)
    draw.line([(cx - half, cy + half), (cx + half, cy + half)],
              fill=tone["rule"], width=10)
    draw.line([(cx - half, cy - half), (cx + half, cy + half)],
              fill=tone["rule"], width=8)
    draw.line([(cx + half, cy - half), (cx - half, cy + half)],
              fill=tone["rule"], width=8)
    # 上面剩 1-part，下面積 part。
    up = (1 - part) * half
    if up > 4:
        draw.polygon([(cx - up, cy - up), (cx + up, cy - up), (cx, cy)],
                     fill=colour)
    down = part * half
    if down > 4:
        draw.polygon([(cx, cy), (cx - down, cy + down), (cx + down, cy + down)],
                     fill=colour)
    at(draw, cx, cy + half + 50, str(spec.get("value", "")), 150, tone["lead"],
       room=W - 2 * MARGIN)
    if spec.get("under"):
        _mid(draw, cy + half + 230, spec["under"], 52, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _clock_dots(spec: dict[str, Any], t: float) -> Image.Image:
    """一圈點，亮到某一個為止。

    連續的弧看不出「幾個」；一圈點數得出來。用在年、次數、輪次上。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    part = float(spec.get("part") or 1) * ease(t)
    centre, radius = (W // 2, top + 330), 220
    count = 24
    for index in range(count):
        angle = math.radians(-90 + index * (360 / count))
        x = centre[0] + radius * math.cos(angle)
        y = centre[1] + radius * math.sin(angle)
        lit = (index / count) < part
        r = 15 if lit else 9
        draw.ellipse([x - r, y - r, x + r, y + r],
                     fill=(spec.get("colour") or tone["hot"]) if lit
                          else tone["rule"])
    at(draw, centre[0], centre[1] - 90, str(spec.get("value", "")), 180,
       tone["lead"], room=radius * 2 - 60)
    if spec.get("under"):
        _mid(draw, centre[1] + radius + 80, spec["under"], 52, tone["dim"],
             bold=False)
    _note(draw, spec, t)
    return card


def _clock_wait(spec: dict[str, Any], t: float) -> Image.Image:
    """一整排的格子代表全部，填到某一格 —— 像月曆上劃掉的日子。

    圓和條都是抽象的量；格子是「一格一天」，具體到可以想像。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    part = float(spec.get("part") or 1) * ease(t)
    across, down = 10, 5
    cell = min(78, (W - 2 * MARGIN) / across)
    left = W / 2 - across * cell / 2
    y0 = top + 150
    total = across * down
    for index in range(total):
        col, row = index % across, index // across
        x, y = left + col * cell, y0 + row * cell
        lit = (index / total) < part
        draw.rounded_rectangle([x + 5, y + 5, x + cell - 5, y + cell - 5], 8,
                               fill=(spec.get("colour") or tone["hot"]) if lit
                                    else _fade(tone["rule"], 0.5, tone["bottom"]))
    at(draw, W // 2, y0 + down * cell + 50, str(spec.get("value", "")), 170,
       tone["lead"], room=W - 2 * MARGIN)
    if spec.get("under"):
        _mid(draw, y0 + down * cell + 250, spec["under"], 52, tone["dim"],
             bold=False)
    _note(draw, spec, t)
    return card


def _chain_down(spec: dict[str, Any], t: float) -> Image.Image:
    """直的，一個點一個點往下走，字在點的右邊。

    橫的線把「順序」壓成一排小字；直的給每一站一整行。
    點多、或名字長的時候，這一種讀得完。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    names = spec.get("points") or []
    x = MARGIN + 70
    step_y = min(150, (H - 700 - top) / max(1, len(names)))
    run = ease(min(1.0, t * 1.3))
    draw.line([(x, top + 90), (x, top + 90 + step_y * (len(names) - 1) * run)],
              fill=tone["rule"], width=8)
    for index, name in enumerate(names):
        y = top + 90 + index * step_y
        if (index / max(1, len(names) - 1)) > run + 0.05:
            continue
        last = index == len(names) - 1
        colour = tone["hot"] if last else tone["cold"]
        draw.ellipse([x - 24, y - 24, x + 24, y + 24], fill=colour)
        size, rows = wrap_at(str(name), 60, W - x - MARGIN - 60, most_rows=2)
        for row_index, row in enumerate(rows):
            draw.text((x + 60, y - size * 0.42 + row_index * (size + 8)), row,
                      font=face(size), fill=colour if last else tone["ink"],
                      anchor="la")
    if spec.get("under"):
        _mid(draw, top + 90 + step_y * len(names) + 60, spec["under"], 54,
             tone["lead"], bold=False)
    _note(draw, spec, t)
    return card


def _chain_steps(spec: dict[str, Any], t: float) -> Image.Image:
    """一階一階往上的台階，每一階一個名字。

    線說的是「接下去」，台階說的是「一次比一次高」—— 用在升高、加碼、
    越演越烈的那種順序上。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    names = spec.get("points") or []
    floor = top + 640
    wide = (W - 2 * MARGIN) / max(1, len(names))
    for index, name in enumerate(names):
        part = stagger(t, index, len(names))
        if part <= 0.02:
            continue
        high = (index + 1) * 110 * part
        x0 = MARGIN + index * wide
        last = index == len(names) - 1
        colour = tone["hot"] if last else tone["cold"]
        draw.rectangle([x0, floor - high, x0 + wide - 8, floor],
                       fill=_fade(colour, 0.45 + 0.55 * part, tone["bottom"]))
        size, rows = wrap_at(str(name), 46, int(wide - 34), most_rows=2)
        for row_index, row in enumerate(rows):
            draw.text((x0 + wide / 2 - 4, floor - high + 22
                       + row_index * (size + 6)), row, font=face(size),
                      fill=tone["ink"] if part > 0.6 else tone["dim"],
                      anchor="ma")
    if spec.get("under"):
        _mid(draw, floor + 60, spec["under"], 54, tone["lead"], bold=False)
    _note(draw, spec, t)
    return card


def _chain_arrows(spec: dict[str, Any], t: float) -> Image.Image:
    """幾個方塊，中間用箭頭串起來。

    點是「站」，方塊是「東西」—— 用在流程、經手的單位、一件事被誰接手過。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    names = spec.get("points") or []
    y = top + 220
    gap = 46
    wide = (W - 2 * MARGIN - gap * max(0, len(names) - 1)) / max(1, len(names))
    for index, name in enumerate(names):
        part = stagger(t, index, len(names))
        if part <= 0.02:
            continue
        x0 = MARGIN + index * (wide + gap)
        last = index == len(names) - 1
        colour = tone["hot"] if last else tone["cold"]
        draw.rounded_rectangle([x0, y, x0 + wide, y + 190], 16,
                               outline=colour, width=6)
        size, rows = wrap_at(str(name), 48, int(wide - 30), most_rows=3)
        start = y + 95 - len(rows) * (size + 8) / 2
        for row_index, row in enumerate(rows):
            draw.text((x0 + wide / 2, start + row_index * (size + 8)), row,
                      font=face(size), fill=tone["ink"], anchor="ma")
        if index and part > 0.3:
            ax = x0 - gap + 8
            draw.line([(ax, y + 95), (ax + gap - 22, y + 95)],
                      fill=tone["rule"], width=6)
            draw.polygon([(ax + gap - 12, y + 95), (ax + gap - 30, y + 84),
                          (ax + gap - 30, y + 106)], fill=tone["rule"])
    if spec.get("under"):
        _mid(draw, y + 260, spec["under"], 54, tone["lead"], bold=False)
    _note(draw, spec, t)
    return card


def _chain_track(spec: dict[str, Any], t: float) -> Image.Image:
    """一條粗軌道，名字交錯排在上下兩側。

    交錯讓每一站有兩倍的橫向空間 —— 名字長的時候，這是唯一還能保持
    「一條線」形狀的排法。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    names = spec.get("points") or []
    y = top + 340
    left, right = MARGIN + 40, W - MARGIN - 40
    run = ease(min(1.0, t * 1.3))
    draw.rounded_rectangle([left, y - 9, left + (right - left) * run, y + 9], 9,
                           fill=tone["rule"])
    gap = (right - left) / max(1, len(names) - 1)
    for index, name in enumerate(names):
        x = left + index * gap
        if (index / max(1, len(names) - 1)) > run + 0.05:
            continue
        last = index == len(names) - 1
        colour = tone["hot"] if last else tone["cold"]
        draw.ellipse([x - 26, y - 26, x + 26, y + 26], fill=colour)
        up = index % 2 == 0
        # 交錯之後每一站可以用到左右兩個半格。
        room = room_at(x, gap * 1.7)
        size, rows = wrap_at(str(name), 54, room, most_rows=2)
        for row_index, row in enumerate(rows):
            oy = (y - 60 - (len(rows) - row_index) * (size + 8)) if up \
                else (y + 60 + row_index * (size + 8))
            draw.text((x, oy), row, font=face(size),
                      fill=colour if last else tone["ink"], anchor="ma")
    if spec.get("under"):
        _mid(draw, y + 320, spec["under"], 54, tone["lead"], bold=False)
    _note(draw, spec, t)
    return card


def _stack_numbered(spec: dict[str, Any], t: float) -> Image.Image:
    """編號的清單，號碼在左邊一個圓圈裡。

    有編號就有順序。用在「三個步驟」「四個理由」那種本來就有先後的清單上；
    沒有面板，行與行之間靠一條細線分開。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    items = spec.get("items") or []
    for index, item in enumerate(items):
        part = stagger(t, index, len(items))
        if part <= 0.02:
            continue
        y = top + 120 + index * 150
        colour = "#" + "".join(f"{v:02x}" for v in
                               _fade(tone["ink"], part, tone["bottom"]))
        draw.ellipse([MARGIN + 20, y, MARGIN + 96, y + 76],
                     outline=spec.get("colour") or tone["hot"], width=6)
        draw.text((MARGIN + 58, y + 12), str(index + 1), font=face(46),
                  fill=spec.get("colour") or tone["hot"], anchor="ma")
        step, rows = wrap_at(str(item), 58, W - MARGIN * 2 - 130, most_rows=2)
        for row_index, row in enumerate(rows):
            draw.text((MARGIN + 130, y + 8 + row_index * (step + 8)), row,
                      font=face(step), fill=colour, anchor="la")
        if index < len(items) - 1:
            draw.line([(MARGIN + 130, y + 118), (W - MARGIN, y + 118)],
                      fill=_fade(tone["rule"], 0.6 * part, tone["bottom"]),
                      width=3)
    _note(draw, spec, t)
    return card


def _stack_tick(spec: dict[str, Any], t: float) -> Image.Image:
    """每一條前面打一個勾，勾是畫出來的。

    勾是「這一項成立」。用在盤點、確認、條件都滿足了那種清單上 ——
    面板是中性的，勾有立場。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    items = spec.get("items") or []
    for index, item in enumerate(items):
        part = stagger(t, index, len(items))
        if part <= 0.02:
            continue
        y = top + 120 + index * 140
        mark = spec.get("colour") or tone["hot"]
        # 勾兩筆，第二筆比第一筆長 —— 一次畫完的勾看起來像符號，不像動作。
        first = min(1.0, part * 2)
        draw.line([(MARGIN + 24, y + 44), (MARGIN + 24 + 30 * first, y + 74)],
                  fill=mark, width=10)
        if part > 0.5:
            second = (part - 0.5) * 2
            draw.line([(MARGIN + 54, y + 74),
                       (MARGIN + 54 + 58 * second, y + 74 - 62 * second)],
                      fill=mark, width=10)
        step, rows = wrap_at(str(item), 58, W - MARGIN * 2 - 150, most_rows=2)
        colour = "#" + "".join(f"{v:02x}" for v in
                               _fade(tone["ink"], part, tone["bottom"]))
        for row_index, row in enumerate(rows):
            draw.text((MARGIN + 150, y + 14 + row_index * (step + 8)), row,
                      font=face(step), fill=colour, anchor="la")
    _note(draw, spec, t)
    return card


def _stack_cascade(spec: dict[str, Any], t: float) -> Image.Image:
    """一階一階往右下錯開，像疊在桌上的卡片。

    錯開讓「先後」有厚度：第一張最上面、最左邊，後面的壓在下面。
    用在「一層一層加上去」的清單上。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    items = spec.get("items") or []
    step_x = min(46, (W - 2 * MARGIN - 420) / max(1, len(items)))
    for index, item in enumerate(items):
        part = stagger(t, index, len(items))
        if part <= 0.02:
            continue
        # 往右下錯開，而寬度跟著縮 —— 不縮的話最後一張會壓在右邊界上。
        left = MARGIN + 40 + index * step_x
        right = W - MARGIN - 40
        y = top + 110 + index * 128 - (1 - part) * 26
        draw.rounded_rectangle([left, y, right, y + 108], 16,
                               fill=_fade(tone["rule"], 0.5 * part,
                                          tone["bottom"]))
        step, rows = wrap_at(str(item), 54, int(right - left - 60), most_rows=1)
        draw.text((left + 30, y + 24), rows[0], font=face(step),
                  fill="#" + "".join(f"{v:02x}" for v in
                                     _fade(tone["ink"], part, tone["bottom"])),
                  anchor="la")
    _note(draw, spec, t)
    return card


def _stack_quote(spec: dict[str, Any], t: float) -> Image.Image:
    """每一條前面一道豎線，像引述。

    用在「他們各自怎麼說」那種清單上 —— 豎線是引號的簡寫，讀起來是
    好幾個人在講話，不是一份規格。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    top = _heading(draw, spec, t)
    items = spec.get("items") or []
    for index, item in enumerate(items):
        part = stagger(t, index, len(items))
        if part <= 0.02:
            continue
        y = top + 120 + index * 156
        step, rows = wrap_at(str(item), 56, W - MARGIN * 2 - 120, most_rows=2)
        high = len(rows) * (step + 10) + 16
        draw.line([(MARGIN + 30, y), (MARGIN + 30, y + high * part)],
                  fill=spec.get("colour") or tone["hot"], width=7)
        colour = "#" + "".join(f"{v:02x}" for v in
                               _fade(tone["ink"], part, tone["bottom"]))
        for row_index, row in enumerate(rows):
            draw.text((MARGIN + 70, y + row_index * (step + 10)), row,
                      font=face(step), fill=colour, anchor="la")
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
        # 字要待在那個面板裡，不是待在畫面裡 —— 面板從 200 到 W-200，
        # 所以可用寬度是 W-400 再扣左右的內縮，比留白窄得多。
        at(draw, W // 2, y + 30, str(item), 58,
           "#" + "".join(f"{v:02x}" for v in
                         _fade(tone["ink"], part, tone["bottom"])),
           room=W - 400 - 60)
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
    # Tighter than the rest: the ring is drawn around it and needs the room.
    size = fits([text], 260, room=W - 2 * MARGIN - 200)
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


def _ring_box(spec: dict[str, Any], t: float) -> Image.Image:
    """方框，四個角先出現，然後四條邊補起來。

    圈是手畫的、隨性的；方框是有人蓋章的。同一個詞，兩種態度。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    if spec.get("title"):
        _mid(draw, 520, spec["title"], 58, tone["dim"], bold=False)
    text = str(spec.get("value", ""))
    size = fits([text], 250, room=W - 2 * MARGIN - 180)
    _mid(draw, 720, text, size, tone["ink"])
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    wide = ruler.textlength(text, font=face(size)) + 130
    box = [W // 2 - wide / 2, 690, W // 2 + wide / 2, 690 + size + 90]
    edge = spec.get("colour") or tone["hot"]
    corner = min(90.0, wide / 3)
    grow = ease(min(1.0, t * 1.6))
    # 先四個角，再往中間長 —— 一次畫完整個框就只是個邊界。
    for x0, y0, dx, dy in ((box[0], box[1], 1, 1), (box[2], box[1], -1, 1),
                           (box[0], box[3], 1, -1), (box[2], box[3], -1, -1)):
        draw.line([(x0, y0), (x0 + dx * corner * grow, y0)], fill=edge, width=9)
        draw.line([(x0, y0), (x0, y0 + dy * corner * grow)], fill=edge, width=9)
    if grow > 0.9:
        rest = ease((t - 0.6) / 0.4) if t > 0.6 else 0
        high = box[3] - box[1]
        # 四條邊都要補。本來只補了上下，於是框在 t=1 還是缺左右兩段 ——
        # 而那不是「還在畫」，是「畫不完」。
        for y in (box[1], box[3]):
            draw.line([(box[0] + corner, y),
                       (box[0] + corner + (wide - 2 * corner) * rest, y)],
                      fill=edge, width=9)
        for x in (box[0], box[2]):
            draw.line([(x, box[1] + corner),
                       (x, box[1] + corner + (high - 2 * corner) * rest)],
                      fill=edge, width=9)
    if spec.get("under"):
        _mid(draw, box[3] + 70, spec["under"], 52, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _ring_under(spec: dict[str, Any], t: float) -> Image.Image:
    """底線，從左往右畫過去，尾巴甩出去一點。

    最輕的一種強調 —— 用在「這個詞值得注意」而不是「這個詞是答案」。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    if spec.get("title"):
        _mid(draw, 540, spec["title"], 58, tone["dim"], bold=False)
    text = str(spec.get("value", ""))
    size = fits([text], 280, room=W - 2 * MARGIN - 60)
    _mid(draw, 740, text, size, tone["ink"])
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    wide = ruler.textlength(text, font=face(size))
    run = ease(max(0.0, (t - 0.3) / 0.7))
    if run > 0.02:
        left = W // 2 - wide / 2 - 14
        # 1.02 倍剛好是字的底線，線會壓在筆畫上。往下讓 12%。
        y = 740 + size * 1.14
        # 兩筆，粗細不同，尾端多甩 30px —— 一筆到底看起來是印上去的。
        draw.line([(left, y), (left + (wide + 28) * run, y)],
                  fill=spec.get("colour") or tone["hot"], width=13)
        if run > 0.8:
            draw.line([(left + wide * 0.6, y + 12),
                       (left + (wide + 58) * run, y + 6)],
                      fill=spec.get("colour") or tone["hot"], width=6)
    if spec.get("under"):
        _mid(draw, 740 + size + 130, spec["under"], 52, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _ring_arrow(spec: dict[str, Any], t: float) -> Image.Image:
    """一支箭從旁邊指過來。

    圈和框都是「把它框住」，箭是「有人在指」—— 多一個方向，也多一個
    「這是被挑出來的」的暗示。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    if spec.get("title"):
        _mid(draw, 520, spec["title"], 58, tone["dim"], bold=False)
    text = str(spec.get("value", ""))
    # 右邊留給箭，所以字能用的寬度少 260。
    size = fits([text], 240, room=W - 2 * MARGIN - 300)
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    wide = ruler.textlength(text, font=face(size))
    at(draw, W // 2 - 110, 730, text, size, tone["ink"],
       room=int(W - 2 * MARGIN - 300))
    fly = ease(max(0.0, (t - 0.35) / 0.65))
    if fly > 0.02:
        colour = spec.get("colour") or tone["hot"]
        tip = W // 2 - 110 + wide / 2 + 40
        tail = min(W - MARGIN, tip + 300)
        now = tail - (tail - tip) * fly
        y = 730 + size * 0.5
        draw.line([(tail, y), (now, y)], fill=colour, width=11)
        if fly > 0.7:
            draw.polygon([(now, y), (now + 34, y - 22), (now + 34, y + 22)],
                         fill=colour)
    if spec.get("under"):
        _mid(draw, 730 + size + 120, spec["under"], 52, tone["dim"], bold=False)
    _note(draw, spec, t)
    return card


def _ring_burst(spec: dict[str, Any], t: float) -> Image.Image:
    """字的四周射出短線，像漫畫裡的「叮」。

    最吵的一種。用在荒謬、意外、好笑的那一句上 —— 圈和框都太正經。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    if spec.get("title"):
        _mid(draw, 500, spec["title"], 58, tone["dim"], bold=False)
    text = str(spec.get("value", ""))
    size = fits([text], 250, room=W - 2 * MARGIN - 260)
    ruler = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    wide = ruler.textlength(text, font=face(size))
    centre = (W // 2, 760 + size * 0.45)
    pop = ease(max(0.0, (t - 0.25) / 0.75))
    if pop > 0.02:
        colour = spec.get("colour") or tone["hot"]
        # 起點要在字的外面。0.62 倍字高還在字身上，於是短線從筆畫中間
        # 長出來，看起來像畫壞了。
        near_x, near_y = wide / 2 + 46, size * 0.86
        for step in range(12):
            angle = math.radians(step * 30 + 15)
            # 橢圓形排開，長短交錯 —— 一樣長的話看起來像時鐘。
            far = 1.0 + 0.42 * (step % 2)
            x0 = centre[0] + near_x * math.cos(angle)
            y0 = centre[1] + near_y * math.sin(angle)
            x1 = centre[0] + near_x * far * math.cos(angle) * pop
            y1 = centre[1] + near_y * far * math.sin(angle) * pop
            draw.line([(x0, y0), (x1, y1)], fill=colour, width=9)
    _mid(draw, 760, text, size, tone["ink"])
    if spec.get("under"):
        _mid(draw, 760 + size + 150, spec["under"], 52, tone["dim"], bold=False)
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
    was_size, now_size = fits([was], 96), fits([now], 130)
    faded = _fade(tone["dim"], 1 - 0.55 * ease(max(0.0, (t - 0.4) / 0.6)),
                  tone["top"])
    _mid(draw, top + 90, was, was_size,
         "#" + "".join(f"{v:02x}" for v in faded))
    if t > 0.35:
        width = draw.textlength(was, font=face(was_size))
        strike = ease((t - 0.35) / 0.35)
        draw.line([(W / 2 - width / 2, top + 150),
                   (W / 2 - width / 2 + width * min(1, strike), top + 150)],
                  fill=tone["hot"], width=10)
    arrive = ease(max(0.0, (t - 0.5) / 0.5))
    if arrive > 0.01:
        _mid(draw, top + 300 + (1 - arrive) * 40, now, now_size,
             "#" + "".join(f"{v:02x}" for v in
                           _fade(tone["lead"], arrive, tone["top"])))
    _note(draw, spec, t)
    return card


def _brand(card: Image.Image, draw: ImageDraw.ImageDraw,
           tone: dict[str, Any], t: float) -> None:
    """The channel mark and the ask, bottom right of the last frame.

    Bottom right and above the caption band, because below that is YouTube's
    own furniture and anything put there is covered. It arrives last, after
    the message has landed: someone who has just understood something will
    look at what made them understand it, and that half second is the only
    moment anybody subscribes.

    A missing icon file draws a placed circle rather than nothing. Drawing
    nothing is how the credit line disappeared from a finished film without
    anyone noticing.
    """
    brand = rules_module.look("brand", {}) or {}
    begin = float(brand.get("show_from", 0.35))
    show = ease(max(0.0, (t - begin) / max(0.05, 1 - begin)))
    if show <= 0.02:
        return
    size = int(brand.get("icon_size", 168))
    right = W - int(brand.get("corner_right", 74))
    bottom = int(brand.get("corner_bottom", 1300))
    box = [right - size, bottom - size, right, bottom]

    icon = ROOT / str(brand.get("icon", ""))
    if icon.is_file():
        badge = Image.open(icon).convert("RGBA").resize((size, size))
        round_mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(round_mask).ellipse([0, 0, size - 1, size - 1], fill=255)
        card.paste(badge, (box[0], box[1]), round_mask)
        # A ring around it. Without one the avatar's own edge dissolves into
        # whatever colour is behind it, and this is watched on a phone at
        # arm's length: a mark is either legible or it should not be there.
        if brand.get("ring", True):
            ImageDraw.Draw(card).ellipse(box, outline=tone["lead"], width=6)
    else:
        # Drawn, not typed: PingFang has no ▶ and a missing glyph renders as
        # nothing at all, which is exactly the failure this placeholder is
        # here to make visible.
        draw.ellipse(box, outline=tone["lead"], width=5)
        middle = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        arm = size * 0.22
        draw.polygon([(middle[0] - arm * 0.7, middle[1] - arm),
                      (middle[0] - arm * 0.7, middle[1] + arm),
                      (middle[0] + arm, middle[1])], fill=tone["lead"])

    call = str(brand.get("call") or "請訂閱")
    size_call = 52
    font = face(size_call)
    wide = draw.textlength(call, font=font)
    right_edge = box[0] - 26
    middle = (box[1] + box[3]) / 2
    if brand.get("plate", True):
        # White on a bright card and white on a dark one are different
        # problems; a plate solves both, and it is the same device the
        # captions already use, so the frame does not gain a new idiom.
        draw.rounded_rectangle(
            [right_edge - wide - 30, middle - size_call * 0.72,
             right_edge, middle + size_call * 0.78],
            26, fill=tone["lead"])
        draw.text((right_edge - 15, middle - size_call * 0.58), call, font=font,
                  fill=tone["top"] if isinstance(tone["top"], str) else "#101820",
                  anchor="ra")
    else:
        draw.text((right_edge, middle - size_call * 0.58), call, font=font,
                  fill=tone["ink"], anchor="ra")
    handle = str(brand.get("handle") or "")
    if handle:
        draw.text((right_edge, middle + size_call * 0.9), handle,
                  font=face(32, False), fill=tone["dim"], anchor="ra")


def _outro(spec: dict[str, Any], t: float) -> Image.Image:
    """The last page: what the film argued, then the sentence to take away.

    One line was not enough. Somebody arriving at the end of ninety seconds
    has been given a dozen facts in the order that made the argument work, and
    the order that makes an argument work is not the order somebody can carry
    out of the room. So the ending restates it: three or four steps, then the
    sentence that survives them.

    It is built out of time rather than space -- the steps arrive one after
    another, the conclusion lands after the last of them, the channel mark
    after that. A page with all of it printed at once is a wall, and the frame
    has room for either the steps or the sentence, not both at full size.
    """
    card, draw = _base(spec)
    tone = tone_of(spec)

    points = [str(one) for one in (spec.get("points") or []) if str(one).strip()]
    span = 0.5 if points else 0.0          # how much of the shot the recap takes
    y = TOP + 30
    for index, point in enumerate(points):
        part = stagger(min(1.0, t / max(span, 0.01)), index, len(points))
        if part <= 0.02:
            continue
        size = fits([point], 58, room=W - 2 * MARGIN - 90)
        draw.text((MARGIN + 78, y + index * 96 - (1 - part) * 26), point,
                  font=face(size, False),
                  fill="#" + "".join(f"{v:02x}" for v in
                                     _fade(tone["ink"], part * 0.9, tone["top"])),
                  anchor="la")
        # A rule down the left, growing with the list: it reads as one thing
        # being counted off rather than four separate captions.
        draw.line([(MARGIN + 40, y + index * 96 - 6),
                   (MARGIN + 40, y + index * 96 + 60)],
                  fill=tone["lead"], width=5)

    after = ease(max(0.0, (t - span) / max(0.05, 1 - span)))
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    size = fits(rows, 150)
    top = y + len(points) * 96 + 56
    for index, row in enumerate(rows):
        part = stagger(after, index, len(rows))
        colour = "#" + "".join(f"{v:02x}" for v in
                               _fade(spec.get("colour") or tone["lead"], part,
                                     tone["top"]))
        _mid(draw, top + index * (size + 20) + (1 - part) * 32, row, size, colour)
    if spec.get("under") and after > 0.5:
        _mid(draw, top + len(rows) * (size + 20) + 34, spec["under"], 44,
             tone["dim"], bold=False)
    _brand(card, draw, tone, t)
    _note(draw, spec, t)
    return card


def _outro_ticks(spec: dict[str, Any], t: float) -> Image.Image:
    """摘要每一條前面一個勾，結論落在下面。

    豎線是「這是一串」，勾是「這幾件都成立」—— 用在論證是「檢查了幾件事」
    的片子上。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    points = [str(one) for one in (spec.get("points") or []) if str(one).strip()]
    span = 0.5 if points else 0.0
    y = TOP + 30
    for index, point in enumerate(points):
        part = stagger(min(1.0, t / max(span, 0.01)), index, len(points))
        if part <= 0.02:
            continue
        oy = y + index * 100 - (1 - part) * 24
        mark = tone["lead"]
        draw.line([(MARGIN + 34, oy + 34), (MARGIN + 34 + 22 * min(1, part * 2),
                                            oy + 56)], fill=mark, width=8)
        if part > 0.5:
            grow = (part - 0.5) * 2
            draw.line([(MARGIN + 56, oy + 56),
                       (MARGIN + 56 + 44 * grow, oy + 56 - 48 * grow)],
                      fill=mark, width=8)
        size, rows = wrap_at(point, 56, W - 2 * MARGIN - 130, most_rows=1)
        draw.text((MARGIN + 120, oy), rows[0], font=face(size, False),
                  fill="#" + "".join(f"{v:02x}" for v in
                                     _fade(tone["ink"], part * 0.9, tone["top"])),
                  anchor="la")
    after = ease(max(0.0, (t - span) / max(0.05, 1 - span)))
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    size = fits(rows, 150)
    top = y + len(points) * 100 + 60
    for index, row in enumerate(rows):
        part = stagger(after, index, len(rows))
        _mid(draw, top + index * (size + 20) + (1 - part) * 32, row, size,
             "#" + "".join(f"{v:02x}" for v in
                           _fade(spec.get("colour") or tone["lead"], part,
                                 tone["top"])))
    if spec.get("under") and after > 0.5:
        _mid(draw, top + len(rows) * (size + 20) + 34, spec["under"], 44,
             tone["dim"], bold=False)
    _brand(card, draw, tone, t)
    _note(draw, spec, t)
    return card


def _outro_steps(spec: dict[str, Any], t: float) -> Image.Image:
    """摘要編號，像一份說明書；結論在最下面一整條上。

    編號讓摘要讀起來有順序，而不是四個並列的事實。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    points = [str(one) for one in (spec.get("points") or []) if str(one).strip()]
    span = 0.5 if points else 0.0
    y = TOP + 20
    for index, point in enumerate(points):
        part = stagger(min(1.0, t / max(span, 0.01)), index, len(points))
        if part <= 0.02:
            continue
        oy = y + index * 104 - (1 - part) * 22
        draw.text((MARGIN + 34, oy - 4), str(index + 1), font=face(64),
                  fill=_fade(tone["lead"], part, tone["top"]), anchor="la")
        size, rows = wrap_at(point, 54, W - 2 * MARGIN - 130, most_rows=1)
        draw.text((MARGIN + 112, oy + 6), rows[0], font=face(size, False),
                  fill="#" + "".join(f"{v:02x}" for v in
                                     _fade(tone["ink"], part * 0.9, tone["top"])),
                  anchor="la")
    after = ease(max(0.0, (t - span) / max(0.05, 1 - span)))
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    size = fits(rows, 140, room=W - 2 * MARGIN - 60)
    top = y + len(points) * 104 + 70
    if after > 0.05:
        high = len(rows) * (size + 20) + 60
        draw.rounded_rectangle([MARGIN, top - 30, W - MARGIN, top - 30 + high * after],
                               20, fill=_fade(tone["rule"], 0.55, tone["bottom"]))
    for index, row in enumerate(rows):
        part = stagger(after, index, len(rows))
        _mid(draw, top + index * (size + 20), row, size,
             "#" + "".join(f"{v:02x}" for v in
                           _fade(spec.get("colour") or tone["lead"], part,
                                 tone["top"])))
    if spec.get("under") and after > 0.5:
        _mid(draw, top + len(rows) * (size + 20) + 50, spec["under"], 44,
             tone["dim"], bold=False)
    _brand(card, draw, tone, t)
    _note(draw, spec, t)
    return card


def _outro_lead(spec: dict[str, Any], t: float) -> Image.Image:
    """結論先落下，摘要在它底下小字排開。

    順序顛倒：拿得走的那句話先到，理由在後面。用在結論本身夠強、
    不需要鋪陳的片子上。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    size = fits(rows, 170)
    lead = ease(min(1.0, t * 1.8))
    y = TOP + 40
    for index, row in enumerate(rows):
        part = stagger(lead, index, len(rows))
        _mid(draw, y + index * (size + 22) + (1 - part) * 30, row, size,
             "#" + "".join(f"{v:02x}" for v in
                           _fade(spec.get("colour") or tone["lead"], part,
                                 tone["top"])))
    under = y + len(rows) * (size + 22) + 40
    draw.line([(MARGIN + 40, under), (W - MARGIN - 40, under)],
              fill=tone["rule"], width=5)
    points = [str(one) for one in (spec.get("points") or []) if str(one).strip()]
    for index, point in enumerate(points):
        part = stagger(max(0.0, (t - 0.45) / 0.55), index, len(points))
        if part <= 0.02:
            continue
        step, said = wrap_at(point, 48, W - 2 * MARGIN - 60, most_rows=1)
        draw.text((W // 2, under + 46 + index * 84), said[0],
                  font=face(step, False),
                  fill="#" + "".join(f"{v:02x}" for v in
                                     _fade(tone["dim"], part, tone["top"])),
                  anchor="ma")
    _brand(card, draw, tone, t)
    _note(draw, spec, t)
    return card


def _outro_card(spec: dict[str, Any], t: float) -> Image.Image:
    """整段收在一張卡片上，像遞出去的一張名片。

    摘要和結論裝在同一個框裡 —— 邊界讓它看起來是可以拿走的一件東西，
    而不是畫面上的最後一段字。
    """
    card, draw = _base(spec)
    tone = tone_of(spec)
    points = [str(one) for one in (spec.get("points") or []) if str(one).strip()]
    rows = [row for row in str(spec.get("title", "")).split("\n") if row]
    size = fits(rows, 120, room=W - 2 * MARGIN - 120)
    high = len(points) * 82 + len(rows) * (size + 18) + 150
    top = TOP + 10
    grow = ease(min(1.0, t * 1.5))
    draw.rounded_rectangle([MARGIN, top, W - MARGIN, top + high * grow], 26,
                           outline=tone["lead"], width=6)
    if grow < 0.85:
        _brand(card, draw, tone, t)
        _note(draw, spec, t)
        return card
    for index, point in enumerate(points):
        part = stagger(max(0.0, (t - 0.35) / 0.4), index, len(points))
        if part <= 0.02:
            continue
        step, said = wrap_at(point, 48, W - 2 * MARGIN - 130, most_rows=1)
        draw.text((MARGIN + 60, top + 50 + index * 82), said[0],
                  font=face(step, False),
                  fill="#" + "".join(f"{v:02x}" for v in
                                     _fade(tone["ink"], part * 0.9, tone["top"])),
                  anchor="la")
    after = ease(max(0.0, (t - 0.72) / 0.28))
    base = top + len(points) * 82 + 90
    for index, row in enumerate(rows):
        part = stagger(after, index, len(rows))
        _mid(draw, base + index * (size + 18), row, size,
             "#" + "".join(f"{v:02x}" for v in
                           _fade(spec.get("colour") or tone["lead"], part,
                                 tone["top"])))
    if spec.get("under") and after > 0.5:
        _mid(draw, base + len(rows) * (size + 18) + 30, spec["under"], 44,
             tone["dim"], bold=False)
    _brand(card, draw, tone, t)
    _note(draw, spec, t)
    return card


# 每一種卡有哪些畫法。放在函式都定義完之後 —— 在上面填的話，名字還不存在。
WAYS.update({
    "word":   [_word, _word_left, _word_boxed, _word_mark, _word_quote],
    "title":  [_word, _word_left, _word_boxed, _word_mark, _word_quote],
    "number": [_number, _number_dial, _number_unit, _number_stamp,
               _number_ghost],
    "ring":   [_ring, _ring_box, _ring_under, _ring_arrow, _ring_burst],
    "swap":   [_swap, _swap_slide, _swap_stack, _swap_arrow, _swap_tear],
    "stack":  [_stack, _stack_numbered, _stack_tick, _stack_cascade,
               _stack_quote],
    "bars":   [_bars, _bars_column, _bars_dots, _bars_split, _bars_pair],
    "split":  [_split, _split_scale, _split_two, _split_venn, _split_road],
    "chain":  [_chain, _chain_down, _chain_steps, _chain_arrows, _chain_track],
    "queue":  [_queue, _queue_grid, _queue_pile, _queue_bar, _queue_crowd],
    "clock":  [_clock, _clock_bar, _clock_sand, _clock_dots, _clock_wait],
    "outro":  [_outro, _outro_ticks, _outro_steps, _outro_lead, _outro_card],
})

KINDS = {"title": _title, "outro": _outro, "word": _word, "number": _number, "bars": _bars,
         "split": _split, "chain": _chain, "queue": _queue, "stack": _stack,
         "clock": _clock, "ring": _ring, "swap": _swap}


# --- 一種卡，好幾種畫法 -------------------------------------------------
#
# 十二種卡各只有一種長相，於是一支九十秒的片裡同一種形狀會出現六七次，
# 每次一模一樣。單張看不出來，排成接觸表就是投影片 —— `samey` 那道門攔的是
# 「連續三張同一種」，攔不了「整支片每張 word 都長一樣」。
#
# 所以每一種可以有好幾個畫法，`WAYS` 列在下面。第一個是原本那個。



def _off() -> set[str]:
    """被關掉的畫法。從 `assets/cards.json` 讀，不在程式裡。

    畫法是程式，關不關掉是選擇 —— 兩件事分開，才可能在網頁上關掉一個
    而不用改程式。
    """
    where = ROOT / "assets" / "cards.json"
    if not where.is_file():
        return set()
    try:
        return set(json.loads(where.read_text(encoding="utf-8")).get("off") or [])
    except Exception:                                             # noqa: BLE001
        return set()


def way_for(spec: dict[str, Any]):
    """這張卡用哪一個畫法。

    **從卡片自己的內容算出來，不是隨機。** 隨機的話同一份文案重壓兩次會長得
    不一樣，接觸表看到的跟成品不同，而過了門的那一版可能根本不是畫出來的那版。
    雜湊給的是「散開但固定」：不同的卡分到不同的畫法，同一張卡永遠同一個。
    """
    kind = str(spec.get("kind") or "title")
    ways = WAYS.get(kind) or [KINDS.get(kind, _word)]
    off = _off()
    live = [one for one in ways if f"{kind}.{one.__name__}" not in off] or ways[:1]
    mark = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    pick = int(hashlib.sha1(mark.encode("utf-8")).hexdigest(), 16)
    return live[pick % len(live)]


def draw(spec: dict[str, Any], t: float = 1.0) -> Image.Image:
    """One card at a moment in its own arrival. An unknown kind becomes a word
    card rather than an error: a script naming a shape nobody has drawn yet
    should still render."""
    return way_for(spec)(spec, t)


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


def how() -> str:
    """A fingerprint of what decides how a card looks.

    The specification is only half of it: this module and the theme decide the
    rest. Naming a drawn card after the specification alone meant the page
    went on serving a picture drawn by code that no longer exists -- the
    channel mark was enlarged and the still on the page never changed, because
    the file was there and the file was wrong. The rendered film had exactly
    the same fault for exactly the same reason.
    """
    here = Path(__file__)
    marks = [str(here.stat().st_mtime_ns),
             json.dumps(rules_module.theme(), sort_keys=True)]
    return hashlib.sha1("|".join(marks).encode("utf-8")).hexdigest()[:8]


def name_for(spec: dict[str, Any], suffix: str = ".png") -> str:
    """A filename that changes when the card does -- or when the drawing does."""
    body = json.dumps(spec, ensure_ascii=False, sort_keys=True) + how()
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12] + suffix


def render(script_name: str, spec: dict[str, Any]) -> str:
    """The finished card as a still, for the page."""
    here = CARD_DIR / script_name
    here.mkdir(parents=True, exist_ok=True)
    target = here / name_for(spec)
    if not target.is_file():
        draw(spec, 1.0).save(target)
    return str(target.relative_to(ROOT))


def sweep(script_name: str, keep: set[str]) -> int:
    """Drop cards this script no longer refers to.

    Here rather than in `render`, which sees one card and cannot know what the
    rest of the script still points at. Every edit to a card, and every change
    to this module, leaves the previous drawing behind unreachable.
    """
    here = CARD_DIR / script_name
    if not here.is_dir():
        return 0
    gone = 0
    for old in here.glob("*.png"):
        if old.name not in keep:
            old.unlink(missing_ok=True)
            gone += 1
    return gone


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
