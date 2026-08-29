"""The script: what will be said, in order, with what on screen and where it
came from.

Everything else follows from this. The narration decides the length, the
length decides how many lines fit, and each line decides what has to be on
screen while it is spoken. Written the other way round -- pictures first, words
fitted afterwards -- you get a clip reel with captions, which is what the
reused-content rules exist to catch.

So a script is not prose. It is a list of lines, each one carrying its own
duration, its own picture, and the source of whatever fact it states. A line
with a claim and no source is a fault, and the page says so.

    90 seconds of Chinese narration is about 400 characters.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "scripts"
SAFE_NAME = re.compile(r"[\w一-鿿][\w一-鿿 -]{0,63}")

# Measured on news-paced Mandarin. Used to turn a script into seconds before
# anything is recorded, so "90 seconds" is arithmetic rather than a hope.
PER_SECOND = 4.5
LIMIT = 90.0


def spoken_length(text: str) -> int:
    """Characters that take time to say. Spaces and Western punctuation do not;
    a Chinese full stop does, because the reader pauses on it."""
    return len(re.sub(r"[\s -/:-@]", "", text or ""))


def line_seconds(line: dict[str, Any]) -> float:
    """How long this line takes. A stated duration wins -- some lines are held
    on a picture longer than they take to read."""
    given = line.get("seconds")
    if given:
        return float(given)
    return round(spoken_length(line.get("say", "")) / PER_SECOND, 2)


def measure(script: dict[str, Any]) -> dict[str, Any]:
    """The script's own arithmetic: length, and whether it fits."""
    lines = script.get("lines") or []
    clock = 0.0
    laid = []
    for line in lines:
        span = line_seconds(line)
        laid.append({**line, "at": round(clock, 2), "seconds": round(span, 2),
                     "characters": spoken_length(line.get("say", ""))})
        clock += span
    said = sum(item["characters"] for item in laid)
    # A line that states a fact and names no source is the fault worth catching
    # early: by the time it is spoken aloud nobody checks it again. A line
    # marked 觀點 is ours and needs no source -- that is the difference between
    # an unsupported claim and an opinion, and the page should not confuse them.
    unsourced = [item["at"] for item in laid
                 if item.get("say") and not item.get("from")]
    opinion = sum(1 for item in laid if item.get("from") == "觀點")
    return {"lines": laid, "seconds": round(clock, 2), "characters": said,
            "over": round(max(0.0, clock - LIMIT), 2), "unsourced": unsourced,
            "opinion": opinion}


def path_for(name: str) -> Path:
    if not SAFE_NAME.fullmatch(name):
        raise ValueError("文案名稱只能用中英文、數字、底線、減號、空白")
    return SCRIPT_DIR / f"{name}.json"


def names() -> list[str]:
    if not SCRIPT_DIR.is_dir():
        return []
    return sorted(path.stem for path in SCRIPT_DIR.glob("*.json"))


def load(name: str) -> dict[str, Any]:
    path = path_for(name)
    if not path.is_file():
        raise FileNotFoundError(f"找不到文案 {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(name: str, script: dict[str, Any]) -> Path:
    path = path_for(name)
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(script, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def listing() -> list[dict[str, Any]]:
    found = []
    for name in names():
        try:
            script = load(name)
        except json.JSONDecodeError:
            continue
        sums = measure(script)
        found.append({
            "name": name,
            "topic": script.get("topic", ""),
            "seconds": sums["seconds"],
            "characters": sums["characters"],
            "over": sums["over"],
            "unsourced": len(sums["unsourced"]),
            "lines": len(sums["lines"]),
            "sources": sum(len(script.get("sources", {}).get(kind) or [])
                           for kind in ("video", "reports", "images")),
            "modified": int(path_for(name).stat().st_mtime),
        })
    return found
