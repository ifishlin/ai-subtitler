"""Draw subtitles as pictures.

The editor and ffmpeg disagree about text. A browser and libass lay out the
same string with different metrics, so a caption that fits on one line in the
preview can wrap in the burn, and no amount of matching font sizes fixes it --
they are two typesetters.

Drawing each caption once, here, removes the disagreement instead of narrowing
it: the browser shows the picture, ffmpeg overlays the same picture, and what
you approve is the artwork that ships. Line breaking becomes ours as well,
which is how Chinese gets to break between words rather than wherever a line
happens to fill up.

Captions are drawn on a strip the width of their box, tall enough for four
lines. Every picture is the same size, which is what lets ffmpeg take them as
one stream of stills rather than one input per cue -- a four-minute video has
more captions than ffmpeg will accept as inputs. Drawing the strip instead of
the whole canvas is what keeps it quick: a full 1920x1080 frame each took half
a minute for a four-minute video, the strip takes a few seconds.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# Measured off a burned frame: libass renders a 20pt caption at 56px on a
# 1920x1080 picture, which is the frame height over 384 -- its correction for a
# 4:3 script drawn on a 16:9 frame. See src/scene.py for the margin scale.
FONT_SCRIPT = 384

# Faces are looked up by name so a scene stays portable; the first file that
# holds the name wins.
FONT_FILES = [
    ("/System/Library/Fonts/PingFang.ttc", {"PingFang HK": 0, "PingFang TC": 1, "PingFang SC": 2}),
    ("/System/Library/Fonts/STHeiti Medium.ttc", {"Heiti TC": 0}),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", {"Noto Sans CJK TC": 0}),
]

LINE_GAP = 0.12          # of the line height, matching the burn's tighter setting
MAX_ROWS = 4             # how many lines the strip leaves room for
CJK = re.compile(r"[　-鿿＀-￯]")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for path, faces in FONT_FILES:
        if name in faces and Path(path).is_file():
            return ImageFont.truetype(path, size, index=faces[name])
    for path, faces in FONT_FILES:                       # any CJK face will do
        if Path(path).is_file():
            return ImageFont.truetype(path, size, index=next(iter(faces.values())))
    return ImageFont.load_default(size)


def _pieces(line: str) -> list[str]:
    """Split into the smallest units a line may break between. Latin keeps its
    trailing space so words rejoin cleanly; Chinese breaks between words, which
    is what jieba is for."""
    out: list[str] = []
    for chunk in re.findall(r"[^　-鿿＀-￯]+|[　-鿿＀-￯]+", line):
        if CJK.search(chunk):
            import jieba
            out.extend(jieba.cut(chunk))
        else:
            out.extend(re.findall(r"\S+\s*|\s+", chunk))
    return out


def _wrap(line: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Break one line to fit `width`, never mid-word."""
    if font.getlength(line) <= width:
        return [line]
    rows, current = [], ""
    for piece in _pieces(line):
        if current and font.getlength(current + piece) > width:
            rows.append(current.rstrip())
            current = piece.lstrip()
        else:
            current += piece
    if current.strip():
        rows.append(current.rstrip())
    return rows or [line]


TIME_LINE = re.compile(
    r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})"
)


def read_srt(path: Path) -> list[dict[str, Any]]:
    """Cues with their line breaks intact. The editor's own reader joins the
    lines, which is right for editing and wrong here: a bilingual cue is two
    lines on purpose, and that is what has to be drawn."""
    def seconds(hours: str, minutes: str, secs: str, millis: str) -> float:
        return (int(hours) * 3600 + int(minutes) * 60 + int(secs)
                + int(millis.ljust(3, "0")) / 1000)

    cues: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        found = next(((i, TIME_LINE.search(l)) for i, l in enumerate(lines)
                      if TIME_LINE.search(l)), None)
        if not found:
            continue
        index, match = found
        cues.append({
            "start": seconds(*match.groups()[:4]),
            "end": seconds(*match.groups()[4:]),
            "text": "\n".join(line.strip() for line in lines[index + 1:]),
        })
    return cues


def style_of(element: dict[str, Any], canvas: tuple[int, int]) -> dict[str, Any]:
    """The drawing settings a subtitle element asks for, in canvas pixels."""
    unit = canvas[1] / FONT_SCRIPT
    return {
        "name": element.get("font", "PingFang TC"),
        "size": round((element.get("size") or 20) * unit),
        "outline": round((element.get("outline_width", 3)) * unit),
        "colour": element.get("colour", "#FFFFFF"),
        "edge": element.get("outline", "#000000"),
        "box": [int(value) for value in element["box"]],
    }


def band_of(style: dict[str, Any]) -> list[int]:
    """Where on the canvas the pictures sit: the caption box, grown upwards to
    hold a wrapped block and sideways to hold the outline."""
    left, _, right, bottom = style["box"]
    font = _font(style["name"], style["size"])
    ascent, descent = font.getmetrics()
    step = round((ascent + descent) * (1 + LINE_GAP))
    edge = style["outline"] + 2
    return [left - edge, bottom - MAX_ROWS * step - edge, right + edge, bottom + descent + edge]


def draw(text: str, style: dict[str, Any]) -> Image.Image:
    """One caption, centred in its box and resting on the box's bottom edge --
    the same anchor libass uses, so replacing one with the other does not move
    the captions."""
    font = _font(style["name"], style["size"])
    left, _, right, bottom = style["box"]
    width = right - left

    rows: list[str] = []
    for line in text.split("\n"):
        rows.extend(_wrap(line.strip(), font, width) if line.strip() else [""])
    rows = rows[-MAX_ROWS:]                     # a strip only holds so many

    ascent, descent = font.getmetrics()
    step = round((ascent + descent) * (1 + LINE_GAP))
    band = band_of(style)
    image = Image.new("RGBA", (band[2] - band[0], band[3] - band[1]), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    # The last row's descender rests on the box's bottom edge; earlier rows
    # stack upwards from it. Coordinates are relative to the strip.
    baseline = bottom - descent - (len(rows) - 1) * step - band[1]
    middle = (left + right) / 2 - band[0]
    for row in rows:
        if row:
            pen.text(
                (middle, baseline), row, font=font,
                fill=style["colour"], anchor="ms",
                stroke_width=style["outline"], stroke_fill=style["edge"],
            )
        baseline += step
    return image


def _name(text: str, style: dict[str, Any]) -> str:
    key = repr((text, sorted(style.items()))).encode("utf-8")
    return f"cap_{hashlib.sha1(key).hexdigest()[:16]}.png"


def build(
    cues: list[dict[str, Any]],
    element: dict[str, Any],
    canvas: tuple[int, int],
    out_dir: Path,
) -> dict[str, Any]:
    """Draw every cue, reusing any picture already on disk. Returns where the
    strip sits on the canvas and which file carries each cue."""
    style = style_of(element, canvas)
    band = band_of(style)
    out_dir.mkdir(parents=True, exist_ok=True)
    drawn = []
    for cue in cues:
        text = str(cue.get("text", "")).strip()
        if not text:
            continue
        name = _name(text, style)
        path = out_dir / name
        if not path.is_file():
            # Compression is the slow part and these are scratch files.
            draw(text, style).save(path, compress_level=1)
        drawn.append({"start": float(cue["start"]), "end": float(cue["end"]),
                      "file": name, "text": text})
    return {"band": band, "captions": drawn}


def blank(band: list[int], out_dir: Path) -> Path:
    """A transparent strip, for the stretches with nothing to say."""
    width, height = band[2] - band[0], band[3] - band[1]
    path = out_dir / f"cap_blank_{width}x{height}.png"
    if not path.is_file():
        out_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(path)
    return path


def playlist(
    drawn: list[dict[str, Any]],
    duration: float,
    band: list[int],
    out_dir: Path,
    offset: float = 0.0,
) -> Path:
    """Write the concat list that turns the pictures into one image stream.

    ffmpeg takes a few dozen inputs comfortably and a hundred and fifty badly,
    so the captions arrive as a single stream of stills with durations rather
    than as an input each. Gaps are the blank frame."""
    empty = blank(band, out_dir).name
    lines, clock = [], 0.0

    def hold(name: str, seconds: float) -> None:
        if seconds <= 0.001:
            return
        lines.append(f"file '{name}'\nduration {seconds:.3f}")

    for cue in sorted(drawn, key=lambda item: item["start"]):
        start, end = cue["start"] - offset, cue["end"] - offset
        if end <= 0 or start >= duration:
            continue
        start, end = max(0.0, start), min(duration, end)
        hold(empty, start - clock)
        hold(cue["file"], end - max(start, clock))
        clock = end
    hold(empty, duration - clock)
    # The concat demuxer ignores the final entry's duration, so it is repeated.
    lines.append(f"file '{lines[-1].split(chr(39))[1] if lines else empty}'")

    path = out_dir / "captions.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
