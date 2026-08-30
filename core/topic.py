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

from core import rules as rules_module

WANT = {"videos": rules_module.at("collect.videos", 5),
        "reports": rules_module.at("collect.reports", 5),
        "images": rules_module.at("collect.images", 15)}

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


def audience_for(name: str, _unused: str = "") -> str:
    """A first guess at who this topic reaches, from its name."""
    # The topic's own words, and only those. It used to include `angle`, which
    # was the same eight characters on every topic -- a field that never varies
    # cannot narrow anything, and it was being fed to a keyword match.
    hay = name
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
        # Doubted sources do not vote. Noise is what makes an unbalanced pile
        # look balanced -- twenty-four irrelevant headlines carry leans too.
        for item in settled(pile, kind):
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


def settled(pile: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """Sources nobody has doubted.

    A report marked `doubt` is still there and still readable; it just does not
    count towards having enough. Counting it would let a search that returned
    an airport malaria story and an architecture review report 報導 28/5 --
    ready to write, on material about something else.
    """
    return [one for one in pile.get("sources", {}).get(kind) or []
            if not one.get("doubt")]


def doubted(pile: dict[str, Any], kind: str = "reports") -> list[dict[str, Any]]:
    """Sources something judged irrelevant and nobody has ruled on yet."""
    return [one for one in pile.get("sources", {}).get(kind) or []
            if one.get("doubt")]


def counts(pile: dict[str, Any]) -> dict[str, Any]:
    got = {kind: len(settled(pile, kind))
           for kind in ("videos", "reports", "images", "data")}
    return {"got": got, "want": WANT, "pictures": picture_mix(pile),
            "short": {kind: max(0, WANT[kind] - got.get(kind, 0)) for kind in WANT}}


def suggest_audience(pile: dict[str, Any]) -> str:
    """A guess from the keyword table, offered rather than shown.

    It used to be what the page displayed, and a table keyed on the topic's
    own words cannot know things: 好萊塢 matched the technology row and the
    page announced that a piece about studios being bought was for people
    whose jobs AI might take. It is for people who pay for streaming.

    A guess presented as an answer is worse than no answer, because nobody
    checks a field that is already filled in.
    """
    return audience_for(pile.get("topic", "") or pile.get("name", ""))


def audience(pile: dict[str, Any]) -> str:
    """Who this topic is for, as decided rather than as guessed.

    Empty until somebody says. The keyword table is in `suggest_audience` and
    is offered on the page as something to click, because a field that arrives
    already filled in is a field nobody reads -- and this one is wrong often
    enough to matter: it is what the whole ending gets written towards.
    """
    return str(pile.get("audience") or "").strip()


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
             # 720p, not 1080p. The short is 1080 wide, so a 1280-wide source
             # is still being scaled down -- and a 27-minute programme we take
             # five seconds out of was arriving as 1.5 GB at the higher size.
             "-f", "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
             "--merge-output-format", "mp4", "--write-auto-subs",
             # `en.*` looked harmless and was not: it matches every
             # auto-translated track YouTube offers -- en-en-uYU..., en-en-JkeT...
             # -- so a dozen subtitle requests go out in a row, the twelfth is
             # refused with 429, and yt-dlp abandons the video it had not yet
             # downloaded. Two names are wanted and both are English originals.
             "--sub-langs", "en,en-orig",
             "--convert-subs", "vtt", "--no-warnings",
             # A missing caption track is not a reason to come back with no
             # video: the frames are still worth having, the video simply
             # stops being a source of chosen ones.
             "--no-abort-on-error",
             "-o", str(here / f"{stem}.%(ext)s")],
            capture_output=True, text=True)
    if not target.is_file():
        return {}
    subs = sorted(here.glob(f"{stem}*.vtt"))
    return {"file": str(target.relative_to(ROOT)),
            "captions": str(subs[0].relative_to(ROOT)) if subs else None,
            "size": target.stat().st_size}


def keywords(pile: dict[str, Any]) -> list[str]:
    """The words to look for in a video's captions.

    Taken from what the collection already knows in English -- the video and
    report titles -- because the captions are English and the topic name is
    not. Common words are dropped: every caption on earth contains "the".
    """
    dull = {"the", "and", "for", "are", "but", "not", "you", "with", "that",
            "this", "from", "have", "has", "how", "why", "what", "who", "will",
            "new", "news", "says", "said", "here", "than", "その"}
    seen: dict[str, int] = {}
    for kind in ("videos", "reports"):
        for item in pile.get("sources", {}).get(kind) or []:
            for word in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", item.get("title", "")):
                low = word.lower()
                if low not in dull:
                    seen[low] = seen.get(low, 0) + 1
    # A word only counts if more than one source used it. Taking the top words
    # outright let a single off-topic result vote for its own vocabulary: a BBC
    # piece about tigers returning to Nepal was collected for a topic about a
    # glacial flood, and "tigers" duly became a keyword, so the video matched
    # the topic it had nothing to do with. Agreement between sources is what
    # makes a word describe the topic rather than one result.
    agreed = [(word, count) for word, count in seen.items() if count > 1]
    ranked = sorted(agreed or seen.items(), key=lambda one: (-one[1], one[0]))
    return [word for word, _ in ranked[:12]]


def wanted_shots(pile: dict[str, Any]) -> list[str]:
    """What this topic still has no picture of.

    Read off the audience rather than the topic. The pile is judged by counts
    -- five of each kind -- and counts cannot tell that a film ending on
    somebody's streaming bill has no picture of a sofa, a remote or a bill,
    because thirty pictures of studios satisfy the number.
    """
    said = str(pile.get("audience") or "")
    if not said:
        return []
    have = " ".join(str(item.get("term", "")).lower()
                    for item in pile.get("sources", {}).get("images") or [])
    # The audience is written in Chinese and the libraries are searched in
    # English, so the bridge is a table -- small, and only for the接觸點 that
    # keep recurring: money, time, health, safety.
    bridge = [
        (("帳單", "電費", "繳費", "付"), ["bill on kitchen table",
                                        "person paying bills laptop"]),
        (("加油", "油價"), ["gas station price sign", "refuelling nozzle close"]),
        (("看病", "醫", "照顧"), ["clinic waiting room", "elderly patient waiting"]),
        (("訂閱", "串流", "看新聞"), ["person watching tv sofa",
                                     "remote control hand couch"]),
        (("淹", "保費", "災"), ["flooded street houses", "insurance paperwork"]),
        (("股", "退休金"), ["stock chart phone", "retirement statement paper"]),
        (("警報", "通知", "簡訊"), ["phone notification screen",
                                   "smoke detector ceiling"]),
        (("役齡", "孩子", "家人"), ["family dinner table", "parent child home"]),
        (("工作", "取代"), ["office worker desk", "empty office chair"]),
    ]
    want = []
    for keys, terms in bridge:
        if any(key in said for key in keys):
            want += [term for term in terms
                     if term.split()[0] not in have]
    return want


def unindexed(pile: dict[str, Any]) -> list[dict[str, Any]]:
    """Videos whose subtitles do not use the topic's own words.

    Named for what it measures rather than what I first wanted it to mean.
    It catches the search noise it was written for -- a BBC piece about tigers
    returning to Nepal, collected for a topic about a glacial flood, which its
    captions betray by mentioning nothing but the country. But it also flags an
    AP piece that is squarely on topic and simply never says so: seven people
    describing what the war did to their week, in the words of somebody talking
    about petrol and parking.

    Both are true of the same thing. Frames and passages are chosen by looking
    for the topic's words in the captions, so a video that does not use them
    cannot be indexed that way whether or not it is about the subject. It is
    still usable by hand; it is not usable by the part of this that runs
    without a person.
    """
    words = keywords(pile)
    if not words:
        return []
    least = 3
    stray = []
    for video in pile.get("sources", {}).get("videos") or []:
        if not video.get("file") or not video.get("captions"):
            continue
        said = " ".join(cue["text"] for cue in cues_of(video)).lower()
        # By stem, not by word. `nepal` and `nepal's` are one thing, and the
        # video this check exists to catch scored two on exactly that pair.
        hits = {word.rstrip("'s").rstrip("'") for word in words if word in said}
        if len(hits) < least:
            stray.append({"outlet": video.get("outlet", ""),
                          "title": video.get("title", "")[:60],
                          "hits": sorted(hits),
                          "why": f"字幕只用到 {len(hits)} 個題目的詞"
                                 f"（{'、'.join(sorted(hits)) or '零'}），"
                                 f"至少要 {least} 個 —— 沒辦法用字幕替它挑畫面。"
                                 f"可能是搜到不相干的片子，也可能只是它講得比較白話"})
    return stray


def cues_of(video: dict[str, Any]) -> list[dict[str, Any]]:
    """This video's subtitles, or nothing if it was published without any."""
    from core import captions as caption_module
    track = video.get("captions")
    return caption_module.read(ROOT / track) if track else []


def frame_moments(video: dict[str, Any], words: list[str],
                  most: int = 4) -> list[dict[str, Any]]:
    """When to take a still from this video, and why that second.

    Evenly spaced sampling is what this replaces. It gave the titles, the
    anchor's face, and two people sitting on stools, because a broadcast cuts
    every few seconds and the shot that illustrates the story is not at 1/4,
    2/4, 3/4. The captions say when the story is being told.

    A video with no captions returns nothing: it is not a frame source. Most
    topics have five videos and at least three of them are subtitled, which is
    enough, and guessing is what got us here.
    """
    from core import captions as caption_module
    cues = cues_of(video)
    if not cues:
        return []
    return caption_module.moments(cues, words, most=most)


def clip_passages(video: dict[str, Any], words: list[str],
                  want: float = 5.0, most: int = 3) -> list[dict[str, Any]]:
    """Stretches of this video worth cutting as moving pictures.

    Half of the borrowed time is meant to move. A still is right when the
    audience has to read something -- a bill, a meter, a graph -- and wrong
    when the point is that something is happening, which a photograph of a
    crowd cannot say. Cut on caption boundaries so it does not open or close
    mid-sentence.
    """
    from core import captions as caption_module
    cues = cues_of(video)
    if not cues:
        return []
    return caption_module.passages(cues, words, want=want, most=most)


def cut_frames(name: str, video: dict[str, Any],
               at: list[float] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stills from a topic's own video: the event as it was broadcast.

    Each moment may be a bare second or a record from `frame_moments`, in
    which case the caption being spoken is kept with the picture. That line is
    the nearest thing a frame has to the caption a stock photograph arrives
    with -- it says what was being talked about, which is not proof of what is
    on screen but is a great deal better than the filename.
    """
    import subprocess
    source = ROOT / video["file"]
    if not source.is_file():
        return []
    here = ROOT / "assets" / "photos" / name
    here.mkdir(parents=True, exist_ok=True)
    stem = Path(video["file"]).stem
    made = []
    for one in at:
        moment = float(one["at"]) if isinstance(one, dict) else float(one)
        said = one.get("said", "") if isinstance(one, dict) else ""
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
            # What was being said at that second, if we know. The title was
            # standing in for this, and a title is the same for every frame in
            # the video -- so it told you nothing about which one you had.
            "caption": said[:160] or video.get("title", "")[:80],
            "said": said, "at": round(moment, 2),
            "source": video.get("file", ""),
            "outlet": video.get("outlet", ""), "author": "",
            "credit": f"畫面來源：{video.get('outlet', '')}",
            "page": video.get("url", "")})
    return made


def hunt(name: str, queries: list[str], most: int = 2,
         say=None) -> list[dict[str, Any]]:
    """Ask each outlet on the list what it said about this.

    The per-outlet round was the part of gathering that only ever ran from the
    command line, which meant the page could not do the thing this project
    argues for -- asking nineteen named outlets rather than searching the web,
    so every answer arrives knowing who gave it.
    """
    import subprocess
    import urllib.parse
    known = {one["name"]: one for one in media()["outlets"]}
    have = {v.get("url") for v in load(name)["sources"]["videos"]}
    least, ceiling = at_seconds()
    found: list[dict[str, Any]] = []
    outlets = [one for one in known.values() if one.get("youtube")]
    for index, spec in enumerate(outlets, start=1):
        if say:
            say(index, len(outlets), f"問 {spec['name']}")
        kept = 0
        for query in queries:
            if kept >= most:
                break
            link = (f"https://www.youtube.com/{spec['youtube']}/search?query="
                    + urllib.parse.quote(query))
            try:
                said = subprocess.run(
                    [str(ROOT / ".venv/bin/yt-dlp"), link, "--flat-playlist",
                     "--playlist-end", "5", "--no-warnings",
                     "--print", "%(duration)s|%(title)s|%(webpage_url)s"],
                    capture_output=True, text=True, timeout=90).stdout
            except Exception:                                     # noqa: BLE001
                continue
            for row in said.strip().splitlines():
                bits = row.split("|", 2)
                if len(bits) != 3 or not bits[0].isdigit():
                    continue
                secs, title, url = int(bits[0]), bits[1], bits[2]
                if not least <= secs <= ceiling or url in have or kept >= most:
                    continue
                have.add(url)
                kept += 1
                found.append({"title": title, "url": url, "seconds": secs,
                              "outlet": spec["name"],
                              "lean": spec.get("lean", "")})
    return found


NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
ITEM = re.compile(r"<item>(.*?)</item>", re.S)
TITLE = re.compile(r"<title>(.*?)</title>", re.S)
WHEN = re.compile(r"<pubDate>(.*?)</pubDate>", re.S)
LINK = re.compile(r"<link>(.*?)</link>", re.S)


def hunt_reports(name: str, queries: list[str], most: int = 2,
                 say=None) -> list[dict[str, Any]]:
    """Ask each outlet what it wrote, the same way we ask what it broadcast.

    Reports were the half of gathering that had no code at all. `hunt` asked
    nineteen YouTube channels and nothing asked the nineteen websites, so the
    balance check could never pass on its own -- every report in this project
    was found by hand and typed in. The button said 逐家問 and meant half of it.

    Searched through Google News restricted to each outlet's own domain, which
    is a keyless endpoint and is the same shape as the video round: the answer
    arrives already knowing who wrote it, because we asked that publication and
    no other. Searching the web instead returns whatever agrees with itself.
    """
    import html as html_module
    import time as time_module
    import urllib.parse
    import urllib.request
    from core import stock as stock_module

    outlets = [one for one in media()["outlets"] if one.get("site")]
    have = {r.get("url") for r in load(name)["sources"].get("reports") or []}
    seen_titles = {r.get("title") for r in load(name)["sources"].get("reports") or []}
    found: list[dict[str, Any]] = []
    for index, spec in enumerate(outlets, start=1):
        if say:
            say(index, len(outlets), f"問 {spec['name']} 的官網")
        kept = 0
        for query in queries:
            if kept >= most:
                break
            site = spec["site"].split("/")[0]
            link = NEWS_RSS.format(query=urllib.parse.quote(
                f"site:{site} {query}"))
            try:
                page = urllib.request.urlopen(
                    urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"}),
                    timeout=25, context=stock_module._ssl_context()
                ).read().decode("utf-8", "replace")
            except Exception:                                     # noqa: BLE001
                continue
            for block in ITEM.findall(page):
                if kept >= most:
                    break
                title = TITLE.search(block)
                if not title:
                    continue
                said = html_module.unescape(title.group(1))
                # Google News appends the outlet to every headline; strip it so
                # the title reads as the outlet wrote it.
                said = re.sub(r"\s+-\s+[^-]{2,40}$", "", said).strip()
                url = (LINK.search(block).group(1).strip()
                       if LINK.search(block) else "")
                if not url or url in have or said in seen_titles:
                    continue
                # A section index, not an article. Google News returns these
                # for outlets whose search pages are themselves indexed --
                # PBS answered with 「art」 and 「italy」, CNN with 「CNN
                # Newsroom」. A headline about one event is not four letters.
                if len(said) < 18 or said.lower() in ("cnn newsroom", "transcripts"):
                    continue
                have.add(url)
                seen_titles.add(said)
                kept += 1
                found.append({
                    "title": said, "url": url, "outlet": spec["name"],
                    "lean": spec.get("lean", ""),
                    "when": (WHEN.search(block).group(1)[:16].strip()
                             if WHEN.search(block) else ""),
                    "paywall": bool(spec.get("paywall"))})
            time_module.sleep(0.4)
    return found


def at_seconds() -> tuple[int, int]:
    """How long a usable video is. Too short has nothing to cut; too long is
    usually a whole programme repeated."""
    span = rules_module.at("collect.video_seconds", [120, 2400])
    return int(span[0]), int(span[1])


def replace_images(name: str, fresh: list[dict[str, Any]]) -> dict[str, int]:
    """Swap a topic's pictures for a new set, old ones removed only after.

    Written after a re-collection deleted forty-seven photographs and then
    failed on its third fetch, leaving a topic whose JSON listed pictures that
    no longer existed. The order was the whole mistake: clear, then gather.
    Anything that throws in between takes the originals with it.

    So the new set arrives complete, is written, and only then is what nothing
    points at any more swept. A failure before that costs nothing but time --
    the topic still has every picture it had.
    """
    pile = load(name)
    keep = {item.get("file") for item in fresh if item.get("file")}
    dropped = [item for item in pile["sources"]["images"]
               if item.get("file") and item["file"] not in keep]
    pile["sources"]["images"] = fresh
    save(name, pile)                      # the record first, the files after
    gone = 0
    for item in dropped:
        target = ROOT / item["file"]
        if target.is_file():
            target.unlink()
            gone += 1
    return {"kept": len(fresh), "removed": gone}


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


def blank(name: str, note: str = "") -> dict[str, Any]:
    """A new topic.

    No `angle`. Every topic carried one and every one of them said 影響民眾生活,
    because it defaulted to that and nobody ever changed it -- a field that
    never varies is not information, and this one was being read by a keyword
    match and printed on the page as though it meant something. What it was
    reaching for is covered twice over: 說給誰聽 says who, and a script's
    `view` says what the argument is.

    `note` stays, and is the thing that cannot be computed: why this is worth
    ninety seconds, and what to watch out for. Whoever chooses the topic writes
    it -- me now, the call that proposes topics later.
    """
    # No `scripts` key. There used to be one, initialised to [] here and
    # written to by nobody, while every page read it -- so eight topics
    # holding ten scripts between them all reported 文案 0, for as long as
    # anyone cared to look. A stored copy of something derivable does not
    # merely risk drifting: this one was never right for a single moment.
    return {"topic": name, "note": note, "made": int(time.time()),
            "sources": {"videos": [], "reports": [], "images": [], "data": []},
            "facts": [], "voices": []}


def _made_from(name: str) -> tuple[list[str], int]:
    """The scripts written from this topic, and how many became films.

    Derived from the scripts' own `topic` field every time, never stored. The
    list is what the page shows and what refuses a delete; the count is what
    tells a topic that produced something from one that stalled, which is the
    difference that matters when there are hundreds.
    """
    from core import script as script_module
    written = script_module.for_topic(name)
    films = sum(1 for one in written
                if (ROOT / "assets" / "shorts" / f"{one}.mp4").is_file())
    return written, films


def listing() -> list[dict[str, Any]]:
    found = []
    for name in names():
        try:
            pile = load(name)
        except json.JSONDecodeError:
            continue
        enough, why = ready(pile)
        written, films = _made_from(name)
        found.append({
            "name": name, "note": pile.get("note", ""),
            "counts": counts(pile)["got"], "balance": balance(pile),
            "ready": enough, "why": why,
            "scripts": written,
            "facts": len(pile.get("facts") or []),
            "leads": len(pile.get("leads") or []),
            "audience": audience(pile),
            # Out of the way rather than gone. A topic that came to nothing is
            # still the record of having asked -- which outlets covered it, how
            # far it got, why it stopped -- and that is worth more than the
            # room it takes in a list.
            "archived": bool(pile.get("archived")),
            "films": films,
            "voices": sum(len(v.get("comments") or []) for v in pile.get("voices") or []),
            "modified": int(path_for(name).stat().st_mtime),
        })
    return found
