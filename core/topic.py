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

WANT = {"videos": 5, "reports": 5, "images": 15}

# Three kinds of picture, five of each, because they cover different holes and
# one cannot stand in for another.
PICTURES = {
    "stock": ("示意圖", 5),   # Pexels: a bill, a meter, a queue -- the abstract
    "real":  ("真實人事地", 5),  # Commons: this person, this street, this building
    "frame": ("新聞畫格", 5),   # cut from the topic's own videos: the event itself
}

# Who a topic actually reaches. The audience is not always "everyone" -- for a
# market story it is a shareholder, and his contact point is an account
# balance, not the price of vegetables. Written down so the ending is aimed
# rather than assumed.
AUDIENCE = [
    (("國債", "通膨", "電費", "物價", "稅"), "每個要付帳單的人"),
    (("股市", "財報", "升息", "降息", "股價", "投資"), "股民、有退休金帳戶的人"),
    (("戰爭", "外交", "軍事", "制裁", "石油"), "加油、繳稅、家裡有役齡孩子的人"),
    (("醫療", "醫師", "醫生", "家醫", "看病", "長照", "健保", "藥", "診"),
     "排隊看病的人、照顧家人的人"),
    (("氣候", "洪災", "地震", "颱風", "天災"), "住在會淹的地方、保費會漲的人"),
    (("AI", "科技", "資料中心", "自動化"), "工作可能被取代的人、電費在漲的人"),
]


def audience_for(name: str, angle: str = "") -> str:
    """A first guess at who this topic reaches, from its name."""
    hay = f"{name}{angle}"
    for words, who in AUDIENCE:
        if any(word in hay for word in words):
            return who
    return ""


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
    sides = {"left": 0, "right": 0, "neutral": 0, "other": 0}
    for kind in ("videos", "reports"):
        for item in pile.get("sources", {}).get(kind) or []:
            lean = item.get("lean") or _lean_of(item.get("outlet", ""), outlets)
            if "left" in lean:
                sides["left"] += 1
            elif "right" in lean:
                sides["right"] += 1
            elif lean in ("neutral",):
                sides["neutral"] += 1
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


def picture_mix(pile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """How many of each kind of picture, and how many are still wanted."""
    images = pile.get("sources", {}).get("images") or []
    mix = {}
    for kind, (label, want) in PICTURES.items():
        have = sum(1 for item in images if item.get("kind", "stock") == kind)
        mix[kind] = {"label": label, "have": have, "want": want,
                     "short": max(0, want - have)}
    return mix


def counts(pile: dict[str, Any]) -> dict[str, Any]:
    got = {kind: len(pile.get("sources", {}).get(kind) or [])
           for kind in ("videos", "reports", "images", "data")}
    return {"got": got, "want": WANT, "pictures": picture_mix(pile),
            "short": {kind: max(0, WANT[kind] - got.get(kind, 0)) for kind in WANT}}


def audience(pile: dict[str, Any]) -> str:
    return pile.get("audience") or audience_for(pile.get("topic", ""),
                                                pile.get("angle", ""))


def ready(pile: dict[str, Any]) -> tuple[bool, str]:
    """Whether there is enough here to write from, and what is missing."""
    lacking = counts(pile)["short"]
    even = balance(pile)
    gaps = [f"還缺 {n} 支影片" for k, n in lacking.items() if n and k == "videos"]
    gaps += [f"還缺 {n} 篇報導" for k, n in lacking.items() if n and k == "reports"]
    gaps += [f"還缺 {n} 張{spec['label']}"
             for spec in picture_mix(pile).values() if (n := spec["short"])]
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


def footage(name: str) -> Path:
    return ROOT / "assets" / "footage" / name


def bring_in(name: str, video: dict[str, Any]) -> dict[str, Any]:
    """Download one of a topic's videos, with its captions.

    Kept because the real pictures come from here. Stock photographs stand in
    for the abstract -- a bill, a meter, a queue -- but the person who said the
    thing, the street it happened on, and the graphic the broadcaster put up
    are only available where they were broadcast.
    """
    import subprocess
    here = footage(name)
    here.mkdir(parents=True, exist_ok=True)
    stem = video["url"].rsplit("=", 1)[-1].rsplit("/", 1)[-1][:24]
    target = here / f"{stem}.mp4"
    if not target.is_file():
        subprocess.run(
            [str(ROOT / ".venv/bin/yt-dlp"), video["url"], "--no-playlist",
             "-f", "bv*[height<=1080]+ba/b[height<=1080]",
             "--merge-output-format", "mp4", "--write-auto-subs",
             "--sub-langs", "en.*", "--convert-subs", "vtt", "--no-warnings",
             "-o", str(here / f"{stem}.%(ext)s")],
            capture_output=True, text=True)
    if not target.is_file():
        return {}
    subs = sorted(here.glob(f"{stem}*.vtt"))
    return {"file": str(target.relative_to(ROOT)),
            "captions": str(subs[0].relative_to(ROOT)) if subs else None,
            "size": target.stat().st_size}


def cut_frames(name: str, video: dict[str, Any], at: list[float]) -> list[dict[str, Any]]:
    """Stills from a topic's own video: the event as it was broadcast.

    A frame is the cheapest kind of borrowed picture -- no seam, no audio, and
    it can be held as long as the line needs -- so most of a short's borrowed
    budget goes further as frames than as clips. It is still borrowed, and the
    credit says so.
    """
    import subprocess
    source = ROOT / video["file"]
    if not source.is_file():
        return []
    here = ROOT / "assets" / "photos" / name
    here.mkdir(parents=True, exist_ok=True)
    stem = Path(video["file"]).stem
    made = []
    for moment in at:
        target = here / f"frame_{stem}_{int(moment)}.jpg"
        if not target.is_file():
            subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{moment:.2f}", "-i", str(source),
                 "-frames:v", "1", "-vf", "scale=1600:-2", "-q:v", "3",
                 str(target), "-y"], capture_output=True)
        if not target.is_file():
            continue
        made.append({
            "id": target.stem, "kind": "frame",
            "term": f"{video['outlet']} {int(moment)}s",
            "file": str(target.relative_to(ROOT)),
            "caption": video.get("title", "")[:80],
            "outlet": video.get("outlet", ""), "author": "",
            "credit": f"畫面來源：{video.get('outlet', '')}",
            "page": video.get("url", "")})
    return made


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
            "leads": len(pile.get("leads") or []),
            "audience": audience(pile),
            "voices": sum(len(v.get("comments") or []) for v in pile.get("voices") or []),
            "modified": int(path_for(name).stat().st_mtime),
        })
    return found
