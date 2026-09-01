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
        # 編號是唯一的介面。`startswith` 不夠 —— 它只處理「寫對的那種」，
        # 而寫錯的那種（一個 dict、一個檔名、一段秒數）從旁邊走過去，
        # 一路到成品。`unpicked` 只檢查檔案在不在，而記錯的檔名往往真的存在，
        # 是別的題目的。
        #
        # visual.md 的範例本來就寫著 `{"file": …, "start": 46.0}`，跟
        # script.md 的「寫檔名一定會錯」正面衝突。那份檔案以前沒有被送進
        # prompt，所以沒有人發現；送了之後，模型會照著範例寫。
        if line.get("pic") is not None:
            key = line["pic"]
            if not (isinstance(key, str) and key.startswith("P")):
                raise ValueError(f"第 {index} 句的 pic 要寫編號（P18 這種），"
                                 f"寫的是 {key!r}")
            if key not in picks:
                raise ValueError(f"第 {index} 句的圖片編號 {key} 不在素材裡")
            line["pic"] = picks[key]
        if line.get("clip") is not None:
            cut = line["clip"]
            if not (isinstance(cut, str) and cut.startswith("C")):
                raise ValueError(f"第 {index} 句的 clip 要寫編號（C3 這種），"
                                 f"起訖由程式填，寫的是 {cut!r}")
            if cut not in cuts:
                raise ValueError(f"第 {index} 句的段落編號 {cut} 不在候選裡")
            one = cuts[cut]
            line["clip"] = {"file": one["file"], "start": one["start"],
                            "end": one["end"]}
            line.setdefault("seconds", round(one["seconds"], 2))
        if line.get("stock") is not None:
            key = line["stock"]
            if not (isinstance(key, str) and key.startswith("V")):
                raise ValueError(f"第 {index} 句的 stock 要寫編號（V3 這種），"
                                 f"寫的是 {key!r}")
            if key not in picks:
                raise ValueError(f"第 {index} 句的情境影片編號 {key} 不在素材裡")
            # 起訖不用寫：整支都是同一個畫面，從頭取夠長就好。新聞片段要
            # start／end 是因為「哪一秒」由字幕決定，情境影片沒有那回事。
            line["stock"] = {"file": picks[key]}
            # 池子裡的每一支都在 /broll 上被人看過才留下來的，所以這裡直接
            # 算數 —— 不標的話 `unchecked` 會擋，而那個「請再看一次」是假的。
            line.setdefault("seen", True)
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
    named = [str(one).strip() for one in (got.get("named") or [])
             if str(one).strip()]
    if not videos:
        raise ValueError("回答裡沒有 videos 搜尋詞")
    # These go to YouTube channels and to a stock library, neither of which
    # holds anything in Chinese, so a Chinese term returns nothing at all --
    # silently, which is the worst kind. The prompt says English and a 7B model
    # treats that as advice; it is a fact about the search, so it is checked.
    latin = re.compile(r"[A-Za-z]")
    bad = [one for one in videos + pictures + named if not latin.search(one)]
    if bad:
        raise ValueError("搜尋詞要用英文，這幾個不是：" + "、".join(bad[:6]))
    return {"videos": videos, "pictures": pictures, "named": named,
            "took": round(took, 1), "raw": said[:2000]}


def sift(topic: str, found: list[dict[str, Any]], say=None) -> list[dict[str, Any]]:
    """Ask which of these are not actually about this.

    The division of labour the report round makes obvious: finding is
    mechanical -- ask each outlet's own domain and take what comes -- and
    deciding whether a headline is about this event is a judgement. Searching
    `Messina museum theft` returned an architecture exhibition review and a
    section index called 「art」 alongside four direct hits, and no rule
    separates those without reading them.

    Asked the other way round on purpose. "Which of these thirty-six are
    relevant" makes the model produce a long list of numbers, and a 30B model
    asked exactly that kept twelve at random -- discarding `Warner Bros
    rejects Paramount's hostile bid` from a topic about Paramount bidding for
    Warner Bros. "Which are not about this" asks for the short list, and a
    model that loses its place drops nothing instead of dropping everything.
    That is the right way for this to fail: keeping a wrong report costs a
    line in a list, dropping a right one costs a fact that never existed.

    In batches, because the mistakes above all appeared once the list got long
    enough that the model stopped reading it.
    """
    if not found:
        return []
    from core import topic as topic_module
    pile = topic_module.load(topic)
    who = topic_module.audience(pile) or "（還沒決定）"
    size = rules_module.at("collect.judge_batch", 12)
    doubted: set[int] = set()

    for base in range(0, len(found), size):
        batch = found[base:base + size]
        rows = "\n".join(f"{index}. [{one.get('outlet', '')}] {one.get('title', '')}"
                          for index, one in enumerate(batch, start=1))
        asking = (
            f"題目：{topic}\n"
            f"說給誰聽：{who}\n\n"
            "底下每一筆是某家媒體的標題。**哪幾筆跟這個題目無關？**\n\n"
            "無關的意思是：講的是別的事件、是分類索引頁、或完全另一個主題。\n"
            "同一件事的不同進展、不同角度、反方說法，全部都算相關，不要剔除。\n"
            "如果每一筆都相關，就回空陣列。\n\n"
            f"{rows}\n\n"
            # No example indices. `{"keep": [1, 4, 7]}` was copied back
            # verbatim by a 30B model as its answer for thirty-six headlines.
            f"只輸出 JSON，drop 裡放無關的編號（1 到 {len(batch)}）："
            '{"drop": [...]}')
        if say:
            say(base // size + 1, (len(found) + size - 1) // size,
                f"判斷相關性 {base + 1}-{base + len(batch)} / {len(found)}")
        try:
            said, _ = ask(asking, None)
        except Exception as error:                                # noqa: BLE001
            # Not the same as "it kept everything". The tunnel died during a
            # run and every one of twenty-eight reports was kept, including an
            # architecture review and a story about malaria at an airport --
            # and the job reported 完成. A step that did not happen must not
            # look like a step that decided nothing needed doing.
            raise RuntimeError(f"問不到模型，沒辦法判斷相關性：{error}") from error
        try:
            drop = {int(one) for one in read(said).get("drop") or []}
        except Exception:                                         # noqa: BLE001
            continue        # asked, could not answer: this batch all stays
        # An answer out of range is not an answer about these headlines.
        if any(one < 1 or one > len(batch) for one in drop):
            continue
        # Everything in a batch is not a judgement about the batch.
        if len(drop) == len(batch):
            continue
        doubted.update(base + one for one in drop)

    # Marked, not removed. Asked which of twenty-eight reports were about the
    # theft, a 7B model kept four and threw away six that plainly were --
    # including the Guardian piece this topic started from. Deleting on that
    # judgement loses the article and the fact that anything was judged: the
    # count simply reads lower and looks like a search that found less.
    return [{**one, "doubt": index in doubted}
            for index, one in enumerate(found, start=1)]


def _which_model() -> str:
    """哪一個模型回答的。設定會改，而一份三個月前的文案要看得出是誰寫的。"""
    config = settings_module.load()
    llm = config.get("llm", {})
    which = llm.get("provider", "qwen")
    return str((llm.get(which) or {}).get("model") or which)


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
    # 模型交回來的原文，原樣留著。
    #
    # 存下來的文案是 `fasten()` 之後的：P18 已經變成檔案路徑、秒數已經填好、
    # 出處已經對過。那是好事，但它**不是模型寫的東西** —— 要回答「模型到底
    # 交了什麼」，只能看這一份。
    #
    # 留原文而不是留解析後的結果，因為解析會失敗，而失敗的時候最想看的正是
    # 原文。`read()` 拒絕過抄回範例的答案、拒絕過 `why` 照抄 prompt 的答案，
    # 那兩次都只在工作日誌裡留下一句「解析不了」。
    answered = {"when": int(time.time()), "took": round(took, 1),
                "model": _which_model(), "raw": said}
    draft = fasten(topic, draft)
    draft.update(topic=topic, format=house, narration=False)
    draft["answered"] = answered
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
