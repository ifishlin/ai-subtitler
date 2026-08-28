"""Turn an HTML file into a transparent PNG for the canvas.

A table wants design -- rules, alignment, tabular figures, a colour that means
something -- and design in Pillow is arithmetic. Design in CSS is a stylesheet,
and a browser already knows how to set type. So the picture is authored as HTML
and captured; what lands in img/ is an ordinary image, dragged onto the frame
like any other.

    python make_card.py table.html                 -> img/table.png
    python make_card.py table.html --name figures  -> img/figures.png

The page should paint one element on a transparent background; whatever it
draws is cropped to, so the file arrives with no margin to fight.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
IMAGES = ROOT / "img"
SCALE = 2                    # capture at twice the size, for clean edges

CHROMES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "chromium", "chromium-browser", "google-chrome",
]


def _chrome() -> str:
    for candidate in CHROMES:
        if Path(candidate).is_file() or shutil.which(candidate):
            return candidate
    raise SystemExit("找不到 Chrome 或 Chromium，無法把 HTML 截成圖")


def capture(page: Path, target: Path, width: int = 1400, height: int = 1200) -> Path:
    """Render `page` and save what it drew, trimmed to its own bounds."""
    raw = target.with_suffix(".raw.png")
    process = subprocess.Popen([
        _chrome(), "--headless=new", "--disable-gpu", "--no-sandbox",
        "--default-background-color=00000000",          # keep the page's alpha
        f"--window-size={width * SCALE},{height * SCALE}",
        "--force-device-scale-factor=1",
        "--virtual-time-budget=4000",
        f"--user-data-dir={target.parent / '.chrome'}",
        f"--screenshot={raw}", page.resolve().as_uri(),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        if raw.is_file() and raw.stat().st_size:
            break
        time.sleep(0.5)
    time.sleep(1.0)
    process.kill()
    if not raw.is_file():
        raise SystemExit("Chrome 沒有產生截圖")

    with Image.open(raw) as shot:
        shot = shot.convert("RGBA")
        box = shot.getbbox()                            # what is not transparent
        cropped = shot.crop(box) if box else shot
        target.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(target)
        size = cropped.size
    raw.unlink()
    print(f"{target}　{size[0]}x{size[1]}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="HTML 轉成畫布可用的透明 PNG")
    parser.add_argument("page", help="要截圖的 HTML 檔")
    parser.add_argument("--name", help="輸出檔名（預設用 HTML 的檔名）")
    parser.add_argument("--out", default=str(IMAGES), help="輸出目錄，預設 img/")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument("--motion", action="store_true",
                        help="Also write <name>.motion.mov: the card's entrance")
    parser.add_argument("--motion-seconds", type=float, default=0.9)
    args = parser.parse_args()

    page = Path(args.page)
    if not page.is_file():
        raise SystemExit(f"找不到 {page}")
    stem = args.name or page.stem
    made = capture(page, Path(args.out) / f"{stem}.png", args.width, args.height)
    if args.motion:
        from core import motion
        with Image.open(made) as card:
            report = motion.render(page.read_text(encoding="utf-8"), card,
                                   made.with_suffix(".motion.mov"),
                                   seconds=args.motion_seconds)
        print(f"{made.with_suffix('.motion.mov')}　{report['frames']} 格 "
              f"{report['seconds']} 秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
