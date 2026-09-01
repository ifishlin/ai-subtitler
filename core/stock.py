"""Stock footage lookup for B-roll.

Pexels and Pixabay both publish a free API over CC0-style libraries: usable
commercially, modifiable, no attribution required. A key is still needed, read
from the environment or a local file so it never reaches the repository.

    export PEXELS_API_KEY=...        # https://www.pexels.com/api/
    export PIXABAY_API_KEY=...       # https://pixabay.com/api/docs/

Search terms must be English; the libraries do not index Chinese. A translated
run already has English to hand, and a Chinese one can pass its terms through
Qwen first.
"""
from __future__ import annotations

import json
import os
import re
import time
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from core import rules as rules_module
from typing import Any

TIMEOUT = 20
KEY_DIR = Path.home() / ".config" / "video_pipeline"
# Pexels rejects urllib's default agent string with 403, so identify properly.
USER_AGENT = "video-pipeline/1.0 (+local subtitle tool)"


def _ssl_context() -> ssl.SSLContext | None:
    """A context that trusts the usual roots.

    A python.org framework build ships without wiring the system keychain in,
    so verification fails on every HTTPS call until certifi's bundle is named
    explicitly. Falling back to the default keeps other installs working.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None


@dataclass
class Clip:
    """One downloadable stock video."""

    provider: str
    id: str
    width: int
    height: int
    duration: float
    url: str                       # direct video file
    page: str                      # human page, for crediting the author
    author: str = ""
    # 挑片子的時候要先看得到它。抓一支 8MB 的影片只為了知道「不是這個」，
    # 一百支就是 800MB 的浪費 —— 縮圖二十幾 KB，看完再決定要不要抓。
    still: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def landscape(self) -> bool:
        return self.width >= self.height

    def describe(self) -> str:
        return (f"{self.provider}:{self.id} {self.width}x{self.height} "
                f"{self.duration:.0f}s by {self.author or '不詳'}")


def _key(provider: str) -> str:
    """The API key, from wherever the settings say it lives."""
    from . import settings
    spec = settings.load().get("stock", {}).get(provider) or {}
    spec = {"key_env": f"{provider.upper()}_API_KEY",
            "key_file": str(KEY_DIR / provider), **spec}
    return settings.secret(spec, provider)


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=_ssl_context()) as response:
        return json.load(response)


def _pick_file(files: list[dict[str, Any]], target_width: int) -> dict[str, Any] | None:
    """The rendition closest to the width we will actually display.

    Downloading 4K to place a clip in a corner wastes minutes of transfer, and
    upscaling a 640px file to full frame looks soft, so pick by intent.
    """
    usable = [f for f in files if f.get("link") and f.get("width")]
    if not usable:
        return None
    at_least = [f for f in usable if f["width"] >= target_width]
    return min(at_least or usable, key=lambda f: abs(f["width"] - target_width))


def search_pexels(query: str, count: int = 5, target_width: int = 1920,
                  min_duration: float = 3.0, orientation: str = "landscape") -> list[Clip]:
    params = urllib.parse.urlencode({
        "query": query, "per_page": min(80, count * 3), "orientation": orientation,
    })
    data = _get_json(f"https://api.pexels.com/videos/search?{params}",
                     {"Authorization": _key("pexels")})
    clips = []
    for item in data.get("videos", []):
        if float(item.get("duration", 0)) < min_duration:
            continue
        chosen = _pick_file(item.get("video_files", []), target_width)
        if not chosen:
            continue
        clips.append(Clip(
            provider="pexels", id=str(item["id"]),
            width=chosen["width"], height=chosen.get("height", 0),
            duration=float(item.get("duration", 0)),
            url=chosen["link"], page=item.get("url", ""),
            author=(item.get("user") or {}).get("name", ""),
            still=item.get("image", ""),
        ))
        if len(clips) >= count:
            break
    return clips


def search_pixabay(query: str, count: int = 5, target_width: int = 1920,
                   min_duration: float = 3.0, orientation: str = "landscape") -> list[Clip]:
    params = urllib.parse.urlencode({
        "key": _key("pixabay"), "q": query, "per_page": min(200, max(3, count * 3)),
        "video_type": "film",
    })
    data = _get_json(f"https://pixabay.com/api/videos/?{params}")
    clips = []
    for item in data.get("hits", []):
        if float(item.get("duration", 0)) < min_duration:
            continue
        # Pixabay names its renditions rather than listing widths in one array.
        files = [
            {**spec, "link": spec.get("url")}
            for spec in (item.get("videos") or {}).values()
            if isinstance(spec, dict)
        ]
        chosen = _pick_file(files, target_width)
        if not chosen:
            continue
        if orientation == "landscape" and chosen.get("height", 0) > chosen["width"]:
            continue
        clips.append(Clip(
            provider="pixabay", id=str(item["id"]),
            width=chosen["width"], height=chosen.get("height", 0),
            duration=float(item.get("duration", 0)),
            url=chosen["link"], page=item.get("pageURL", ""),
            author=item.get("user", ""),
            tags=[t.strip() for t in str(item.get("tags", "")).split(",") if t.strip()],
        ))
        if len(clips) >= count:
            break
    return clips


def search(query: str, providers: tuple[str, ...] = ("pexels", "pixabay"),
           **kwargs: Any) -> list[Clip]:
    """Search each provider in turn, skipping any whose key is missing.

    A missing key is a configuration state, not a failure: one provider
    configured is enough to get footage.
    """
    finders = {"pexels": search_pexels, "pixabay": search_pixabay}
    found: list[Clip] = []
    problems = []
    for name in providers:
        try:
            found.extend(finders[name](query, **kwargs))
        except RuntimeError as error:
            problems.append(str(error))
        except Exception as error:                                # noqa: BLE001
            problems.append(f"{name} 查詢失敗：{error}")
    if not found and problems:
        raise RuntimeError("；".join(problems))
    return found


TAGGED = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    """Commons stores its credit fields as HTML -- an anchor round the author's
    name, a span round the licence. On screen that has to be plain text."""
    return " ".join(TAGGED.sub(" ", html).split())


@dataclass
class Picture:
    """One downloadable photograph."""
    provider: str
    id: str
    url: str
    width: int
    height: int
    author: str = ""
    page: str = ""
    about: str = ""
    licence: str = ""


WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")


def answers(term: str, caption: str) -> float:
    """How much of the search term the picture's own caption actually contains.

    Not a filter, and it cannot be one. `electricity bill` returned a fuse box
    because a stock library matches word by word and the caption said
    "billing"; whole-word matching rejects that, which is right -- and rejects
    the correct picture too, whose caption reads "a five dollar bill and
    receipts" and never says electricity. There is no string test that keeps
    one and drops the other.

    Matching is by prefix, because exact words are useless here: `meter`
    against "meters" and `electric` against "Electrical" are both the same
    word and both fail a whole-word test, which scored every correct picture
    zero along with the wrong ones.

    What is left catches only a total miss -- a caption about something else
    entirely. It does not catch the fuse box, which scores exactly what the
    right picture scores. That is the honest limit of a string test, and the
    reason `seen` exists: the page sorts the doubtful to the front, and
    whether the picture shows the thing stays with something that can see it.
    """
    wanted = {word.lower() for word in WORD.findall(term) if len(word) > 2}
    if not wanted:
        return 1.0
    have = {word.lower() for word in WORD.findall(caption or "")}
    hit = sum(1 for word in wanted
              if any(word.startswith(other) or other.startswith(word)
                     for other in have if min(len(word), len(other)) >= 4))
    return round(hit / len(wanted), 2)


def search_photos(query: str, count: int = 12, least_wide: int = 1200
                  ) -> list[Picture]:
    """Photographs, not footage. A script wants more of these than it will use:
    the picture that suits a line is rarely the one that looked best in the
    search, so the choosing happens later, from a pile."""
    params = urllib.parse.urlencode(
        {"query": query, "per_page": min(80, max(1, count)), "orientation": "landscape"})
    data = _get_json(f"https://api.pexels.com/v1/search?{params}",
                     {"Authorization": _key("pexels")})
    found = []
    for item in data.get("photos", []):
        sources = item.get("src") or {}
        best = sources.get("large2x") or sources.get("large") or sources.get("original")
        if not best or int(item.get("width") or 0) < least_wide:
            continue
        found.append(Picture(
            provider="pexels", id=str(item["id"]), url=best,
            width=int(item.get("width") or 0), height=int(item.get("height") or 0),
            author=item.get("photographer") or "", page=item.get("url") or "",
            about=item.get("alt") or ""))
        if len(found) >= count:
            break
    return found


def wiki_lead(title: str, lang: str = "en") -> list[Picture]:
    """The picture at the top of a Wikipedia article.

    Commons fails in the opposite direction from a stock library. Ask a stock
    library for a concept and it gives you something that matches the words --
    "electricity bill" returned a fuse box, because the caption said "billing".
    Ask Commons for a name and it gives you that person; ask it for a concept
    and it wanders, which is how "server rack" found a bicycle rack.

    So for anything with a name, do not search at all. An encyclopaedia has
    already decided which picture is of this person, and its lead image is
    that decision. One request, no ranking to second-guess, and the licence
    comes back with it.

    Nothing is returned for a subject with no article: that is the honest
    answer, and better than the best match for a name nobody wrote about.
    """
    quoted = urllib.parse.quote(title.replace(" ", "_"), safe="")
    summary = _get_json(
        f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quoted}",
        {"User-Agent": WIKI_AGENT})
    source = (summary.get("originalimage") or {}).get("source")
    if not source:
        return []
    # The REST summary appends its own analytics query string to the URL, and
    # a thumbnail URL carries the width in front of the name. Neither is part
    # of the filename Commons is indexed by.
    file = urllib.parse.unquote(source.split("?", 1)[0].rsplit("/", 1)[-1])
    if "px-" in file:
        file = file.split("px-", 1)[-1]
    time.sleep(WIKI_PAUSE)

    # The article gives the picture; Commons gives the terms it travels under.
    params = urllib.parse.urlencode({
        "action": "query", "format": "json", "prop": "imageinfo",
        "titles": f"File:{file}", "iiprop": "url|size|extmetadata"})
    data = _get_json(f"https://commons.wikimedia.org/w/api.php?{params}",
                     {"User-Agent": WIKI_AGENT})
    pages = (data.get("query") or {}).get("pages") or {}
    info = next((page["imageinfo"][0] for page in pages.values()
                 if page.get("imageinfo")), None)
    meta = (info or {}).get("extmetadata") or {}

    def field(key: str) -> str:
        return _strip_tags(str((meta.get(key) or {}).get("value") or ""))

    return [Picture(
        provider="commons", id=file,
        url=(info or {}).get("url") or source,
        width=int((info or {}).get("width") or (summary.get("originalimage") or {}).get("width") or 0),
        height=int((info or {}).get("height") or (summary.get("originalimage") or {}).get("height") or 0),
        author=field("Artist") or field("Credit"),
        licence=field("LicenseShortName") or "見檔案頁",
        page=(info or {}).get("descriptionurl") or summary.get("content_urls", {}).get("desktop", {}).get("page", ""),
        # The article's own first sentence: what this is, in words somebody
        # wrote on purpose, rather than a filename.
        about=(summary.get("extract") or field("ImageDescription"))[:300])]


def search_commons(query: str, count: int = 6, least_wide: int = 900
                   ) -> list[Picture]:
    """Wikimedia Commons: the real person, the real place, the real building.

    A stock library has no photograph of Maduro or of the village that flooded,
    because those are not concepts. Commons does, freely, and needs no key --
    but almost nothing there is public domain outright: most is CC BY or
    CC BY-SA, which means the author and the licence travel with the picture
    and have to appear on screen. So the credit is carried in the record, not
    left to be looked up later.
    """
    # Tight first, loose second. A quoted phrase keeps "server rack" from
    # matching a bicycle rack, but it also finds nothing for a description like
    # "Trump wearing a hat" -- nobody titles a file that. So the phrase is
    # tried, and if the archive has no such phrase the words are tried
    # separately, which is the right order: precision when it is available,
    # something when it is not.
    data = {}
    for attempt in (f'filetype:bitmap "{query}"', f"filetype:bitmap {query}"):
        params = urllib.parse.urlencode({
            "action": "query", "format": "json", "generator": "search",
            "gsrnamespace": "6", "gsrsearch": attempt,
            "gsrlimit": max(1, count) * 3, "prop": "imageinfo",
            "iiprop": "url|size|extmetadata", "iiurlwidth": "1600",
        })
        data = _get_json(f"https://commons.wikimedia.org/w/api.php?{params}",
                         {"User-Agent": WIKI_AGENT})
        if (data.get("query", {}).get("pages") or {}):
            break
        time.sleep(WIKI_PAUSE)
    found = []
    for page in (data.get("query", {}).get("pages") or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        if int(info.get("width") or 0) < least_wide:
            continue
        licence = (meta.get("LicenseShortName", {}).get("value") or "").strip()
        # Anything demanding permission is not usable without asking, and
        # asking is not automation.
        if "Fair use" in licence or "non-free" in licence.lower():
            continue
        author = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value") or "").strip()
        found.append(Picture(
            provider="commons", id=str(page.get("pageid")),
            url=info.get("thumburl") or info.get("url") or "",
            width=int(info.get("width") or 0), height=int(info.get("height") or 0),
            author=(author or "Wikimedia Commons")[:80],
            page=info.get("descriptionurl") or "",
            licence=licence or "見檔案頁",
            about=_described(meta, page.get("title", ""))))
        if len(found) >= count:
            break
    return found


# 上傳的人填了什麼就用什麼，但**檔名不算說明**。這一欄是「來源自己說這張圖
# 是什麼」，而 `門.jpg` 回答不了那個問題 —— 而且它會讓 ⚠ 在每一張真實照片上
# 都亮，一個永遠亮的警告跟沒有警告一樣。
#
# 這些分類是上傳流程加的，跟圖的內容無關。留著它們會讓說明變成
# 「CC-Zero｜Self-published work｜Files with coordinates missing」。
_NOT_ABOUT = ("CC-", "PD-", "PD ", "GFDL", "License", "Licence", "Self-published",
              "Uploaded with", "Taken with", "Files with", "Images by",
              "Flickr", "reviewed", "CC BY", "Public domain")


def _described(meta: dict, title: str) -> str:
    """這張圖，來源自己怎麼說的。

    三層後備，都在同一個 API 回應裡，不用多打一次：上傳者寫的說明、作品名、
    看起來像在講內容的分類。全都沒有就回空字串 —— **空的要看得出是空的**，
    退回檔名或退回搜尋詞都會讓它看起來像有說明。
    """
    def field(key: str) -> str:
        return _strip_tags(str((meta.get(key) or {}).get("value") or "")).strip()

    said = field("ImageDescription")
    if said and said.lower() != title[5:].lower():
        return said[:300]
    named = field("ObjectName")
    if named and named.lower() != title[5:].lower():
        return named[:300]
    groups = [one.strip() for one in field("Categories").split("|")]
    groups = [one for one in groups
              if one and not any(skip in one for skip in _NOT_ABOUT)]
    return "、".join(groups[:3])[:300]


def looks_like(path: Path) -> int:
    """A perceptual fingerprint: shrink to 8x8 grey and record which pixels sit
    above the average. Two pictures of the same thing differ in a few bits; two
    pictures of different things differ in dozens."""
    from PIL import Image
    with Image.open(path) as opened:
        small = opened.convert("L").resize((8, 8))
    pixels = list(small.getdata())
    average = sum(pixels) / len(pixels)
    bits = 0
    for index, value in enumerate(pixels):
        if value > average:
            bits |= 1 << index
    return bits


SAME_WITHIN = rules_module.at("picture.same_within", 8)


def alike(one: int, other: int, within: int | None = None) -> bool:
    """Whether two fingerprints are the same picture.

    The threshold was 12 and the docstring above claimed different pictures
    differ by dozens of bits. Measured on a real pile, they do not: unrelated
    news frames and stock photographs sit 13 to 16 bits apart, because most of
    them are mid-grey rectangles. Twelve was inside the noise, and a portrait
    of David Zaslav was discarded for resembling a photograph of a television
    remote -- silently, the way every picture fault in this project fails.

    The same picture resized is 0 bits away and cropped 5% is 1 to 6, so eight
    sits in the gap with room on both sides.
    """
    return bin(one ^ other).count("1") <= (
        SAME_WITHIN if within is None else within)


# Wikimedia asks for an agent that says who is calling and how to reach them,
# and answers 429 to anything that does not. It also wants a pause between
# requests: a burst of ten is refused even when ten is a small number.
WIKI_AGENT = ("video-pipeline/1.0 (local subtitle tool; "
              "https://github.com/ifishlin/ai-subtitler)")
WIKI_PAUSE = 1.2


def fetch(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    agent = WIKI_AGENT if "wikimedia.org" in url else USER_AGENT
    if "wikimedia.org" in url:
        time.sleep(WIKI_PAUSE)
    request = urllib.request.Request(url, headers={"User-Agent": agent})
    with urllib.request.urlopen(request, timeout=TIMEOUT,
                                context=_ssl_context()) as reply:
        destination.write_bytes(reply.read())
    return destination


def download(clip: Clip, destination: Path) -> Path:
    """Fetch one clip. The credit line is written alongside it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(clip.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120, context=_ssl_context()) as response, \
            destination.open("wb") as handle:
        while chunk := response.read(1 << 20):
            handle.write(chunk)
    destination.with_suffix(".credit.json").write_text(
        json.dumps({
            "provider": clip.provider, "id": clip.id, "author": clip.author,
            "page": clip.page, "duration": clip.duration,
            "size": [clip.width, clip.height],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination
