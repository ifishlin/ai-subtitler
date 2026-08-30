"""Asking a model for a script, and turning what comes back into one.

The piece that was missing. Everything either side of it existed: the material
is gathered, the prompt is assembled, the gates are written, the page can show
and render the result. What did not exist was the forty lines between them.

Three things happen here, and the order matters.

    ask        send the prompt, take the text back
    read       find the JSON in it, whatever else it said
    fasten     turn P18 into a path and C3 into a passage

`fasten` is the one worth explaining. The prompt asks for the numbers off the
material list rather than filenames, because a filename has to be remembered
and a remembered one is often a real file belonging to another topic -- which
looks entirely plausible until something checks. A number either resolves or
raises here, at the line it came from.

Nothing is saved unless the gates are also run, and the faults come back with
it. A script that fails them all is still worth keeping: it is the evidence
that the gates work.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Any

from core import brief as brief_module
from core import rules as rules_module
from core import script as script_module
from core import settings as settings_module

BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def ask(prompt: str, say=None) -> tuple[str, float]:
    """Send it, wait, take the text back.

    Ollama by default, because the local model is free and this step is worth
    running against a bad model on purpose: if a 7B model returns nonsense and
    every gate passes it, the gates are broken and that is worth knowing before
    a good model hides it.
    """
    config = settings_module.load()
    llm = config.get("llm", {})
    which = llm.get("provider", "qwen")
    started = time.time()

    if which == "qwen":
        spec = llm.get("qwen", {})
        body = json.dumps({
            "model": spec.get("model", "qwen2.5:7b"),
            "prompt": prompt, "stream": False,
            "options": {"temperature": 0.7, "num_ctx": 32768},
        }).encode("utf-8")
        if say:
            say(0, 1, f"問 {spec.get('model')}（本機通道 {spec.get('url')}）")
        with urllib.request.urlopen(urllib.request.Request(
                f"{spec.get('url', 'http://127.0.0.1:11435')}/api/generate",
                body, {"Content-Type": "application/json"}),
                timeout=900) as answer:
            said = json.load(answer).get("response", "")
        return said, time.time() - started

    spec = llm.get("claude", {})
    import anthropic
    client = anthropic.Anthropic(api_key=settings_module.secret(spec, "claude"))
    if say:
        say(0, 1, f"問 {spec.get('model')}")
    back = client.messages.create(
        model=spec.get("model", "claude-opus-5"), max_tokens=8000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}])
    said = "".join(part.text for part in back.content
                   if getattr(part, "type", "") == "text")
    return said, time.time() - started


def read(said: str) -> dict[str, Any]:
    """The JSON in the answer, whatever else came with it.

    Models put prose around it, wrap it in a fence, or apologise first. None of
    that is an error worth failing on -- the object either parses or it does
    not, and saying which is more useful than saying "bad format".
    """
    found = BLOCK.search(said)
    text = found.group(1) if found else said
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("回答裡找不到 JSON")
    return json.loads(text[start:end + 1])


def fasten(topic: str, draft: dict[str, Any]) -> dict[str, Any]:
    """Turn P18 into a path and C3 into a passage.

    The numbers come off the same list the prompt showed, so a wrong one is
    caught here, naming the line it was on -- rather than resolving to a file
    that exists and belongs to another film.
    """
    picks = brief_module.pick(topic)
    cuts = {f"C{index}": one for index, one
            in enumerate(brief_module.sheet(topic)["passages"], start=1)}
    lines = []
    for index, line in enumerate(draft.get("lines") or [], start=1):
        line = dict(line)
        key = str(line.get("pic") or "")
        if key.startswith("P"):
            if key not in picks:
                raise ValueError(f"第 {index} 句的圖片編號 {key} 不在素材裡")
            line["pic"] = picks[key]
        cut = str(line.get("clip") or "")
        if cut.startswith("C"):
            if cut not in cuts:
                raise ValueError(f"第 {index} 句的段落編號 {cut} 不在候選裡")
            one = cuts[cut]
            line["clip"] = {"file": one["file"], "start": one["start"],
                            "end": one["end"]}
            line.setdefault("seconds", round(one["seconds"], 2))
        # Seconds come from the reading pace unless the line pinned its own,
        # so a model that omits them is not thereby writing a nine-second card.
        if "seconds" not in line:
            line["seconds"] = round(max(
                script_module.LEAST_SECONDS,
                script_module.spoken_length(line.get("say", "")) /
                script_module.READ_PER_SECOND), 2)
        lines.append(line)
    draft["lines"] = lines
    return draft


def suggest_terms(topic: str, say=None) -> dict[str, Any]:
    """Ask what to search for.

    The one step still typed by hand, and the one the collecting prompt was
    written for: search terms come from imagining how nineteen broadcasters
    would have titled it, and from what the audience's life contains. Both are
    judgements about the topic, which is what this is for.
    """
    said, took = ask(brief_module.to_collect(topic), say)
    got = read(said)
    videos = [str(one).strip() for one in (got.get("videos") or [])
              if str(one).strip()]
    pictures = [str(one).strip() for one in (got.get("pictures") or [])
                if str(one).strip()]
    if not videos:
        raise ValueError("回答裡沒有 videos 搜尋詞")
    # These go to YouTube channels and to a stock library, neither of which
    # holds anything in Chinese, so a Chinese term returns nothing at all --
    # silently, which is the worst kind. The prompt says English and a 7B model
    # treats that as advice; it is a fact about the search, so it is checked.
    latin = re.compile(r"[A-Za-z]")
    bad = [one for one in videos + pictures if not latin.search(one)]
    if bad:
        raise ValueError("搜尋詞要用英文，這幾個不是：" + "、".join(bad[:6]))
    return {"videos": videos, "pictures": pictures, "took": round(took, 1),
            "raw": said[:2000]}


def sift(topic: str, found: list[dict[str, Any]], say=None) -> list[dict[str, Any]]:
    """Ask which of these are actually about this.

    The division of labour the report round makes obvious: finding is
    mechanical -- ask each outlet's own domain and take what comes -- and
    deciding whether a headline is about this event is a judgement. Searching
    `Messina museum theft` returned an architecture exhibition review and a
    section index called 「art」 alongside four direct hits, and no rule
    separates those without reading them.

    A model that cannot answer leaves everything in: keeping a wrong report is
    a fact nobody cites, and dropping a right one is a fact that never existed.
    """
    if not found:
        return []
    from core import topic as topic_module
    pile = topic_module.load(topic)
    rows = "\n".join(f"{index}. [{one['outlet']}] {one['title']}"
                     for index, one in enumerate(found, start=1))
    asking = (
        f"題目：{topic}\n"
        f"說給誰聽：{topic_module.audience(pile) or '（還沒決定）'}\n\n"
        "底下是逐家搜到的標題。**哪幾篇是在講這個題目本身？**\n\n"
        "留下：直接報導這件事的、以及直接相關的背景分析\n"
        "剔除：同類但不同事件的、分類索引頁、完全無關的\n\n"
        f"{rows}\n\n"
        '只輸出 JSON：{"keep": [1, 4, 7]}')
    if say:
        say(0, 1, f"判斷 {len(found)} 篇哪些相關")
    try:
        said, _ = ask(asking, None)
    except Exception as error:                                    # noqa: BLE001
        # Not the same as "it kept everything". The tunnel died during a run
        # and every one of twenty-eight reports was kept, including an
        # architecture review and a story about malaria at an airport -- and
        # the job reported 完成. A step that did not happen must not look like
        # a step that decided nothing needed doing.
        raise RuntimeError(f"問不到模型，沒辦法判斷相關性：{error}") from error
    try:
        keep = {int(one) for one in read(said).get("keep") or []}
    except Exception:                                             # noqa: BLE001
        return found        # asked, could not answer: keep everything
    # Marked, not removed. Asked which of twenty-eight reports were about the
    # theft, a 7B model kept four and threw away six that plainly were --
    # including the Guardian piece this topic started from. Deleting on that
    # judgement loses the article and the fact that anything was judged: the
    # count simply reads lower and looks like a search that found less.
    #
    # Keeping a wrong report costs a line in a list. Dropping a right one costs
    # a fact that no longer exists, so the doubt is recorded and somebody
    # decides.
    return [{**one, "doubt": index not in keep}
            for index, one in enumerate(found, start=1)]


def write(topic: str, house: str = "argue", name: str | None = None,
          say=None) -> dict[str, Any]:
    """Ask for a script, keep it, and report what the gates say about it."""
    from core import topic as topic_module
    pile = topic_module.load(topic)
    prompt = brief_module.prompt(topic, house)
    said, took = ask(prompt, say)
    if say:
        say(1, 3, f"回來了（{took:.0f}s，{len(said)} 字元），解析中")

    draft = read(said)
    draft = fasten(topic, draft)
    draft.update(topic=topic, format=house, narration=False)
    draft.setdefault("for", topic_module.audience(pile))

    name = name or f"{topic[:10]}-{house}"
    script_module.save(name, draft)
    if say:
        say(2, 3, "存好了，跑檢查")
    measured = script_module.measure(draft)
    faults = {key: measured[key] for key in
              ("unpicked", "unchecked", "undrawn", "uncredited", "samey",
               "unsigned", "shapeless")
              if measured[key]}
    if measured["over"]:
        faults["over"] = measured["over"]
    if say:
        say(3, 3, f"{len(measured['lines'])} 句　{measured['seconds']}s　"
                  f"{sum(len(v) if isinstance(v, list) else 1 for v in faults.values())} 處不合格")
    return {"name": name, "seconds": measured["seconds"],
            "lines": len(measured["lines"]), "took": round(took, 1),
            "faults": faults, "raw": said[:4000]}
