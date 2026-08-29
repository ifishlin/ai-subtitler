"""Turning a card's CSS animation into frames, without guessing at time.

A card is HTML, and a browser can animate HTML. The problem is capturing that
animation: screenshotting a page thirty times a second gives frames spaced by
however long each screenshot took, which is not thirty times a second.

The way out is a property of CSS animations rather than a trick of timing. An
animation that is paused with a negative delay renders at exactly that point in
its own timeline -- `animation-delay: -0.4s` on a paused one-second animation
is the frame at 40%. So instead of watching one card animate, we lay out one
copy per frame, each paused at its own moment, and photograph all of them at
once. The browser is asked a question about layout, which it answers exactly,
rather than a question about time, which it cannot.

The same CSS drives the live preview in the editor, running rather than paused.
There is one animation, described once.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image

FPS = 30
COLUMNS = 6              # of the contact sheet; keeps it well inside any limit

# Every card animates the same way: its direct children arrive in turn. Which
# is why this belongs here and not in each template -- a table's rows and a
# timeline's events are the same thing to the eye, and should behave the same.
MOTION_CSS = """
  /* The panel arrives first and its contents fill it in. Without this the
     first frame is an empty box, which reads as a mistake rather than a start. */
  #card {
    animation: cardIn .28s both cubic-bezier(.2,.7,.3,1);
  }
  @keyframes cardIn {
    from { opacity: 0; transform: scale(.97) }
    to   { opacity: 1; transform: none }
  }
  #card > * {
    animation: cardRise var(--rise, .45s) both cubic-bezier(.2,.7,.3,1);
    animation-delay: calc(var(--step, .1s) * var(--row, 0));
  }
  #card > *:nth-child(1)  { --row: 0 }
  #card > *:nth-child(2)  { --row: 1 }
  #card > *:nth-child(3)  { --row: 2 }
  #card > *:nth-child(4)  { --row: 3 }
  #card > *:nth-child(5)  { --row: 4 }
  #card > *:nth-child(6)  { --row: 5 }
  #card > *:nth-child(7)  { --row: 6 }
  #card > *:nth-child(8)  { --row: 7 }
  @keyframes cardRise {
    from { opacity: 0; transform: translateY(16px) }
    to   { opacity: 1; transform: none }
  }
"""

# Held at one instant instead of playing. The negative delay is what selects
# which instant, and `paused` is what stops it moving on from there.
FREEZE_CSS = """
  #card {
    animation-play-state: paused !important;
    animation-delay: calc(-1 * var(--at) * 1s) !important;
  }
  #card > * {
    animation-play-state: paused !important;
    animation-delay: calc(var(--step, .1s) * var(--row, 0) - var(--at) * 1s)
                     !important;
  }
"""

CHROMES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "chromium", "chromium-browser", "google-chrome",
]


def _chrome() -> str:
    for candidate in CHROMES:
        if Path(candidate).is_file() or shutil.which(candidate):
            return candidate
    raise SystemExit("找不到 Chrome 或 Chromium，無法產生動畫")


def with_motion(html: str) -> str:
    """The card's HTML with the animation added, for previewing and capture."""
    if "cardRise" in html:
        return html
    return html.replace("</style>", MOTION_CSS + "</style>", 1) \
        if "</style>" in html else f"<style>{MOTION_CSS}</style>\n{html}"


def _sheet(html: str, frames: int, size: tuple[int, int]) -> str:
    """One page holding every frame, each an isolated document paused at its
    own moment. Separate documents because a card's ids and selectors are
    written as though it were alone on the page, and here it is not."""
    animated = with_motion(html)
    cells = []
    for index in range(frames):
        at = index / FPS
        page = animated.replace(
            "</style>", f"{FREEZE_CSS}  :root {{ --at: {at:.4f} }}\n</style>", 1)
        cells.append(
            f'<iframe scrolling="no" srcdoc="{page.replace(chr(34), "&quot;")}"'
            f' style="width:{size[0]}px;height:{size[1]}px;border:0"></iframe>')
    return (
        "<meta charset='utf-8'><style>html,body{margin:0;background:transparent}"
        f"#sheet{{display:grid;grid-template-columns:repeat({COLUMNS},{size[0]}px);"
        "gap:0}</style>"
        f"<div id='sheet'>{''.join(cells)}</div>")


def _scratch() -> Path:
    """Where Chrome may keep its profile: derived, disposable, out of the way."""
    here = Path(__file__).resolve().parent.parent / "editor_cache" / "scratch"
    here.mkdir(parents=True, exist_ok=True)
    return here


def _capture(page: Path, shot: Path, width: int, height: int) -> None:
    process = subprocess.Popen([
        _chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
        "--default-background-color=00000000",
        f"--window-size={width},{height}",
        "--force-device-scale-factor=1", "--virtual-time-budget=6000",
        f"--user-data-dir={_scratch() / 'chrome-motion'}",
        f"--screenshot={shot}", page.resolve().as_uri(),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(90):
        if shot.is_file() and shot.stat().st_size:
            break
        time.sleep(0.5)
    time.sleep(1.0)
    process.kill()
    if not shot.is_file():
        raise SystemExit("Chrome 沒有產生動畫的截圖")


def render(html: str, card: Image.Image, target: Path, seconds: float = 0.9,
           work: Path | None = None) -> dict[str, Any]:
    """Write the card's entrance as a video with an alpha channel.

    QuickTime Animation rather than a GIF: 256 colours and a one-bit mask turn
    a soft shadow into a sawtooth, and the whole point of drawing these in a
    browser is that the edges are good.
    """
    frames = max(2, round(seconds * FPS))
    size = card.size
    work = work or target.parent / ".motion"
    work.mkdir(parents=True, exist_ok=True)

    page = work / "sheet.html"
    page.write_text(_sheet(html, frames, size), encoding="utf-8")
    rows = (frames + COLUMNS - 1) // COLUMNS
    shot = work / "sheet.png"
    _capture(page, shot, size[0] * COLUMNS, size[1] * rows)

    with Image.open(shot) as sheet:
        sheet = sheet.convert("RGBA")
        for index in range(frames):
            left = (index % COLUMNS) * size[0]
            top = (index // COLUMNS) * size[1]
            sheet.crop((left, top, left + size[0], top + size[1])).save(
                work / f"f{index:04d}.png")

    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-framerate", str(FPS),
        "-i", str(work / "f%04d.png"),
        "-c:v", "qtrle", "-pix_fmt", "argb", str(target),
    ], check=True)
    return {"file": target.name, "frames": frames,
            "seconds": round(frames / FPS, 3), "width": size[0], "height": size[1]}
