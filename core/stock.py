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
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
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
