"""A topic and everything gathered for it.

The script used to carry its own sources, which put the writing before the
reading. In practice one topic feeds more than one video -- ninety seconds
first to see whether anyone cares, then eight minutes if they do -- and both
draw on the same pile. So the pile is the thing that is kept, and a script is
something made from it.

What makes a pile good enough to write from is not how big it is. It is
whether it contains someone who disagrees. Five articles found by searching
tend to agree with each other, because the first page of results agrees with
itself; a long video built from those is a pamphlet. So the balance is
measured and shown, and a topic that has only heard one side says so.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOPIC_DIR = ROOT / "topics"
MEDIA = ROOT / "assets" / "sources" / "media.json"
SAFE_NAME = re.compile(r"[\w一-鿿][\w一-鿿 -]{0,63}")

WANT = {"videos": 5, "reports": 5}       # what a long video needs to be fair


def media() -> dict[str, Any]:
    """The outlets to read, and the mix to aim for."""
    if not MEDIA.is_file():
        return {"outlets": [], "primary": [], "mix": {}}
    return json.loads(MEDIA.read_text(encoding="utf-8"))


def _lean_of(name: str, outlets: list[dict[str, Any]]) -> str:
    for outlet in outlets:
        if outlet["name"].lower() in (name or "").lower():
            return outlet["lean"]
    return "unknown"


def balance(pile: dict[str, Any]) -> dict[str, Any]:
    """Who has been heard, and who has not.

    Grouped rather than counted one lean at a time: what matters is whether
    both sides are present, not whether there are exactly two Reuters pieces.
    """
    outlets = media().get("outlets", [])
    sides = {"left": 0, "right": 0, "neutral": 0, "business": 0, "other": 0}
    for kind in ("videos", "reports"):
        for item in pile.get("sources", {}).get(kind) or []:
            lean = item.get("lean") or _lean_of(item.get("outlet", ""), outlets)
            if "left" in lean:
                sides["left"] += 1
            elif "right" in lean:
                sides["right"] += 1
            elif lean in ("neutral",):
                sides["neutral"] += 1
            elif lean in ("market-liberal",):
                sides["business"] += 1
            else:
                sides["other"] += 1
    missing = []
    if not sides["left"]:
        missing.append("左")
    if not sides["right"]:
        missing.append("右")
    if sides["neutral"] < 2:
        missing.append("中立不足兩則")
    return {"sides": sides, "missing": missing, "balanced": not missing}


def counts(pile: dict[str, Any]) -> dict[str, Any]:
    got = {kind: len(pile.get("sources", {}).get(kind) or [])
           for kind in ("videos", "reports", "images", "data")}
    return {"got": got, "want": WANT,
            "short": {kind: max(0, WANT[kind] - got.get(kind, 0)) for kind in WANT}}


def ready(pile: dict[str, Any]) -> tuple[bool, str]:
    """Whether there is enough here to write from, and what is missing."""
    lacking = counts(pile)["short"]
    even = balance(pile)
    gaps = [f"還缺 {n} 支影片" for k, n in lacking.items() if n and k == "videos"]
    gaps += [f"還缺 {n} 篇報導" for k, n in lacking.items() if n and k == "reports"]
    gaps += ([f"沒有{'、'.join(even['missing'])}的說法"] if even["missing"] else [])
    return (not gaps), "；".join(gaps)


def read_comments(video_url: str, most: int = 60) -> list[dict[str, Any]]:
    """What people said underneath. Material, not decoration.

    The comments tell you where an ordinary viewer got stuck, which is exactly
    where an explanation belongs; they give you the words people actually use,
    which are not the words a press release uses; and they often carry an angle
    the report left out.

    Names are dropped. We want what was said, not who said it -- their identity
    is theirs, and nothing downstream needs it.
    """
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as room:
        subprocess.run(
            [str(ROOT / ".venv/bin/yt-dlp"), video_url, "--skip-download",
             "--write-comments", "--no-warnings",
             "--extractor-args",
             f"youtube:comment_sort=top;max_comments={most},all,{most}",
             "-o", f"{room}/%(id)s"],
            capture_output=True, text=True)
        found = list(Path(room).glob("*.info.json"))
        if not found:
            return []
        info = json.loads(found[0].read_text(encoding="utf-8"))
    kept = []
    for item in info.get("comments") or []:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        kept.append({"say": text[:600],
                     "likes": int(item.get("like_count") or 0),
                     "when": item.get("_time_text") or "",
                     "reply": bool(item.get("parent") and item["parent"] != "root")})
    kept.sort(key=lambda one: -one["likes"])
    return kept[:most]


def path_for(name: str) -> Path:
    if not SAFE_NAME.fullmatch(name):
        raise ValueError("題目名稱只能用中英文、數字、底線、減號、空白")
    return TOPIC_DIR / f"{name}.json"


def names() -> list[str]:
    if not TOPIC_DIR.is_dir():
        return []
    return sorted(path.stem for path in TOPIC_DIR.glob("*.json"))


def load(name: str) -> dict[str, Any]:
    path = path_for(name)
    if not path.is_file():
        raise FileNotFoundError(f"找不到題目 {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(name: str, pile: dict[str, Any]) -> Path:
    path = path_for(name)
    TOPIC_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def blank(name: str, angle: str = "影響民眾生活") -> dict[str, Any]:
    return {"topic": name, "angle": angle, "made": int(time.time()),
            "sources": {"videos": [], "reports": [], "images": [], "data": []},
            "facts": [], "voices": [], "scripts": []}


def listing() -> list[dict[str, Any]]:
    found = []
    for name in names():
        try:
            pile = load(name)
        except json.JSONDecodeError:
            continue
        enough, why = ready(pile)
        found.append({
            "name": name, "angle": pile.get("angle", ""),
            "counts": counts(pile)["got"], "balance": balance(pile),
            "ready": enough, "why": why,
            "scripts": pile.get("scripts") or [],
            "facts": len(pile.get("facts") or []),
            "voices": sum(len(v.get("comments") or []) for v in pile.get("voices") or []),
            "modified": int(path_for(name).stat().st_mtime),
        })
    return found
