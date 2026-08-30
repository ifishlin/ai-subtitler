"""What the writer is shown before it writes.

The rules that survived this project were the ones a program checked. The
rules that broke were the ones that asked for an action -- open the picture,
lay the candidates out, cut on a sentence boundary -- because a generation is
writing, and going to fetch something is not writing. Telling it again, in
bolder type, does not change that.

So the fix is not another instruction. It is to put the thing in front of it:
every picture with its own caption, every clip passage already cut to a
sentence boundary, every one numbered so a line can name one. Then choosing
correctly is the path of least resistance rather than an errand.

    "請你記得看圖"  →  圖在這，編號 3
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core import rules as rules_module

ROOT = Path(__file__).resolve().parent.parent


def passages_for(pile: dict[str, Any], want: float = 5.0,
                 per_video: int = 3) -> list[dict[str, Any]]:
    """Every stretch of footage worth cutting, already on caption boundaries.

    Offered as a list to pick from rather than as a rule to obey. A start and
    an end typed by hand land mid-sentence; these cannot, because they are the
    boundaries.
    """
    from core import topic as topic_module
    words = topic_module.keywords(pile)
    out = []
    for video in pile["sources"]["videos"]:
        if not video.get("file"):
            continue
        for found in topic_module.clip_passages(video, words, want=want,
                                                most=per_video):
            out.append({**found, "file": video["file"],
                        "outlet": video.get("outlet", ""),
                        "title": video.get("title", "")[:70]})
    return out


def sheet(name: str) -> dict[str, Any]:
    """Everything available for this topic, in the shape a writer needs it."""
    from core import topic as topic_module
    from core import stock as stock_module
    pile = topic_module.load(name)
    pictures = []
    for item in pile["sources"]["images"]:
        pictures.append({
            "file": item.get("file", ""),
            "kind": item.get("kind", "stock"),
            "term": item.get("term", ""),
            # The picture's own words. The half that was never shown, and the
            # half that said `fuse box` while the label said 帳單特寫.
            "caption": item.get("caption", ""),
            "answers": stock_module.answers(item.get("term", ""),
                                            item.get("caption", "")),
            "outlet": item.get("outlet", ""),
            "credit": item.get("credit", ""),
            "at": item.get("at"), "said": item.get("said", ""),
        })
    # Doubtful first: a caption that says nothing the search asked for is
    # either the wrong picture or a picture nobody described, and both want
    # looking at before they want using.
    pictures.sort(key=lambda one: (one["kind"] == "frame",
                                   one["answers"], one["kind"]))
    return {"topic": name,
            "angle": pile.get("angle", ""),
            "audience": topic_module.audience(pile),
            "facts": pile.get("facts") or [],
            "voices": (pile.get("voices") or [])[:20],
            "reports": pile["sources"].get("reports") or [],
            "videos": [{k: v.get(k) for k in
                        ("outlet", "lean", "title", "url", "file", "captions")}
                       for v in pile["sources"]["videos"]],
            "pictures": pictures,
            "passages": passages_for(pile)}


def pick(name: str) -> dict[str, str]:
    """The topic's pictures, keyed the way the brief numbers them.

    A script names a file, and three times now I have named one from memory --
    twice a picture from a different topic entirely, which exists, so the path
    looks plausible right up until the gate reports it missing. The gate does
    catch it, before anything is encoded, and it keeps happening anyway.

    So the writing step takes `pick(topic)["P17"]` instead of typing a path.
    A wrong key raises immediately, at the line being written, rather than
    resolving to a file that belongs to another film.
    """
    found = sheet(name)
    out = {f"P{index}": one["file"]
           for index, one in enumerate(found["pictures"], start=1)}
    out.update({f"C{index}": one["file"]
                for index, one in enumerate(found["passages"], start=1)})
    return out


def as_text(name: str) -> str:
    """The same, written out for a prompt.

    Numbered, because a line has to be able to name one, and a filename is a
    poor thing to ask a model to copy exactly.
    """
    found = sheet(name)
    out = [f"# 題目：{found['topic']}",
           f"角度：{found['angle']}　說給誰聽：{found['audience']}", ""]

    out.append("## 事實（每一條都要指得回出處）")
    for fact in found["facts"]:
        text = fact.get("say") if isinstance(fact, dict) else str(fact)
        whom = fact.get("from", "") if isinstance(fact, dict) else ""
        out.append(f"- {text}　／{whom}")
    out.append("")

    from core import topic as topic_module
    stray = topic_module.unindexed(topic_module.load(name))
    if stray:
        out.append("## 這幾支影片不能用字幕挑畫面")
        for one in stray:
            out.append(f"- {one['outlet']}　{one['title']}")
            out.append(f"  {one['why']}")
        out.append("")

    out.append("## 影片段落　—— 起訖只能從這裡挑，它們已經落在句子邊界上")
    for index, one in enumerate(found["passages"], start=1):
        out.append(f"[C{index}] {one['outlet']}　{one['start']}–{one['end']}s"
                   f"（{one['seconds']}s）")
        out.append(f"      {one['said'][:88]}")
        out.append(f"      file: {one['file']}")
    out.append("")

    out.append("## 照片　—— term 是我們要的，caption 是實際拿到的。"
               "兩行不一致就是選錯了")
    kinds = {"stock": "示意", "real": "真實", "frame": "新聞畫格"}
    for index, one in enumerate(found["pictures"], start=1):
        # Only meaningful for a picture that was searched for. A frame's
        # "term" is a timestamp, so scoring it against its caption compares
        # two unrelated things and warns about every frame in the pile.
        mark = ("　⚠ 說明對不上搜尋詞"
                if one["kind"] != "frame" and one["answers"] < 0.5 else "")
        out.append(f"[P{index}] {kinds.get(one['kind'], one['kind'])}"
                   f"　term: {one['term']}{mark}")
        out.append(f"      caption: {one['caption'][:100]}")
        if one.get("at") is not None:
            out.append(f"      {one['outlet']} 第 {one['at']:.0f} 秒"
                       f"：{one['said'][:70]}")
        out.append(f"      file: {one['file']}")
    out.append("")

    out.append("## 鄉民反應")
    for voice in found["voices"][:12]:
        out.append(f"- {voice.get('text', '')[:90]}")
    return "\n".join(out)


def prompt(name: str, which: str = "script") -> str:
    """A prompt with today's numbers in it and the material under it."""
    body = (ROOT / "assets" / "prompts" / f"{which}.md").read_text(
        encoding="utf-8")
    missing = rules_module.unfilled(body)
    if missing:
        raise RuntimeError(f"{which}.md 要的名字 rules/theme 裡沒有：{missing}")
    return rules_module.fill(body) + "\n\n---\n\n" + as_text(name)
