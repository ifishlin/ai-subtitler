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
            # 用收的時候算好的那一個，不要在這裡重算。收的時候算的是
            # `answers(term, about)` —— 拿圖自己的說明去比；重算拿的是
            # `caption`，而 caption 在沒有說明的時候會退回搜尋詞，於是
            # term 對 term 變成滿分：一張沒有人描述過的圖會顯示「完全吻合」
            # 而且不亮 ⚠。同一個數字算兩遍，這個專案已經為此修過四次。
            "answers": item.get("answers") if item.get("answers") is not None
                       else stock_module.answers(item.get("term", ""),
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
            "note": pile.get("note", ""),
            "audience": topic_module.audience(pile),
            "facts": topic_module.facts_of(pile),
            "voices": (pile.get("voices") or [])[:20],
            # Doubted sources do not go to the writer. They were excluded from
            # the count already, and letting them into the prompt would be the
            # same pile arriving by another door: twenty-five irrelevant
            # headlines dilute the material a script is written from, and a
            # model reading an airport malaria story alongside the theft is
            # less likely to see the theft clearly, not more. Rescuing a
            # wrongly doubted source is a button on the page, not a job for
            # the prompt.
            "reports": topic_module.settled(pile, "reports"),
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
           f"說給誰聽：{found['audience'] or '（還沒決定）'}", ""]
    if found["note"]:
        out.append(f"挑這題的理由：{found['note']}")
        out.append("")

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
        # 沒有 file 那一行。編號才是介面：`fasten()` 拿 C3 去 `sheet()` 查
        # 檔案，prompt 裡那行路徑從來沒有人讀。而它不只是浪費 —— script.md
        # 明寫「寫檔名一定會錯」，送檔名進去正好在邀請那個錯。
    out.append("")

    # 舊的標題寫「兩行不一致就是選錯了」，而那句話是錯的：
    # 「Server room of BalticServers」對「data center」字面上完全不一致，
    # 圖卻完全正確。caption 是拿來讀的，不是拿來比對的。
    out.append("## 照片　—— term 是我們要的，caption 是來源自己的說明。"
               "**照 caption 判斷這張圖是什麼**，不要照 term")
    kinds = {"stock": "示意", "real": "真實", "frame": "新聞畫格"}
    for index, one in enumerate(found["pictures"], start=1):
        # Only meaningful for a picture that was searched for. A frame's
        # "term" is a timestamp, so scoring it against its caption compares
        # two unrelated things and warns about every frame in the pile.
        # 記號短一個字都好：這一行會出現六十四次。為什麼要看它，標題那一行
        # 已經說了（term 是我們要的，caption 是實際拿到的）。
        # ⚠ 只留給「根本沒有說明」—— 那件事沒有歧義，而且真的擋人：
        # 沒有說明就沒有任何人知道那張圖是什麼。
        #
        # 字面對不上**不是**同一件事。`answers()` 比的是共同的字，而
        # 「Server room of BalticServers」對上搜尋詞「data center」得 0 分，
        # 那是一張完全正確的機房照片；說明還有義大利文、法文、波蘭文的。
        # 這樣算下來 ⚠ 在 62% 的真實照片上亮，包含好的那些 ——
        # 而 MISTAKES.md 第一條就寫著：分不開的檢查比沒有檢查更糟，
        # 因為它會被相信。所以它降級成一句敘述，講它真的量到的事。
        if not (one["caption"] or "").strip():
            mark = "　⚠ 沒有說明"
        elif one["kind"] != "frame" and one["answers"] < 0.5:
            mark = "　（字面沒對上，看說明）"
        else:
            mark = ""
        out.append(f"[P{index}] {kinds.get(one['kind'], one['kind'])}"
                   f"　term: {one['term']}{mark}")
        # 空的要寫出來是空的。一行 `caption:` 後面什麼都沒有，讀起來像
        # 「說明很短」；寫「沒有說明」它才知道自己是在瞎猜。
        said = one["caption"][:70] or "（來源沒有寫說明，這張圖的內容不明）"
        out.append(f"      caption: {said}")
        if one.get("at") is not None:
            out.append(f"      {one['outlet']} 第 {one['at']:.0f} 秒"
                       f"：{one['said'][:70]}")
    out.append("")

    out.append("## 鄉民反應")
    for voice in found["voices"][:12]:
        out.append(f"- {voice.get('text', '')[:90]}")
    return "\n".join(out)


def to_collect(name: str) -> str:
    """What to ask for, before there is anything to ask about.

    The gathering prompt was the one nothing ever assembled: search terms were
    typed by hand, so nothing in the program connected the topic's audience to
    the pictures it needs -- and that connection is real. A piece about studios
    being bought, aimed at people who pay for streaming, wants a sofa, a
    remote, an empty cinema and a bill; aimed at people whose jobs AI might
    take it wants a set, an actor, a synthesised face. Same topic, different
    pile.

    I was making that connection by reading the field and thinking. That works
    exactly as long as the writer is me.
    """
    from core import topic as topic_module
    pile = topic_module.load(name)
    body = (ROOT / "assets" / "prompts" / "collect.md").read_text(encoding="utf-8")
    # Which language the outlets publish in. A German local story searched in
    # English returns nothing from tagesschau and MDR -- silently, and a silent
    # nothing looks exactly like an outlet that did not cover it.
    body = body.replace("{search.language}", topic_module.search_language(pile))
    missing = rules_module.unfilled(body)
    if missing:
        raise RuntimeError(f"collect.md 要的名字 rules／theme 裡沒有：{missing}")

    said = ["", "---", "", f"# 題目：{name}",
            f"說給誰聽：{topic_module.audience(pile) or '（還沒決定）'}"]
    if pile.get("note"):
        said.append(f"挑這題的理由：{pile['note']}")
    said += ["", "**搜尋詞要照「說給誰聽」那一欄去想** —— 那群人的生活裡有什麼，"
                 "文案就會需要什麼畫面。", ""]

    have = pile.get("sources", {})
    if have.get("videos") or have.get("reports"):
        said.append("## 已經收到的（不要重複）")
        for kind, label in (("videos", "影片"), ("reports", "報導")):
            for item in have.get(kind) or []:
                said.append(f"- {label}　{item.get('outlet', '')}　"
                            f"{item.get('title', '')[:64]}")
        said.append("")
    counts = topic_module.counts(pile)
    said.append("## 還缺")
    said.append("、".join(counts["short"]) if counts["short"] else "（都齊了）")
    return rules_module.fill(body) + "\n".join(said)


def prompt(name: str, house: str = "argue") -> str:
    """A prompt for one house style, with today's numbers and the material.

    Which prompt to send is the format's own business -- an argument gets
    script.md, a story gets story.md -- so naming the format is enough.
    """
    spec = rules_module.house(house)
    which = str(spec.get("prompt") or "script.md").removesuffix(".md")
    body = (ROOT / "assets" / "prompts" / f"{which}.md").read_text(
        encoding="utf-8")
    missing = rules_module.unfilled(body, house)
    if missing:
        raise RuntimeError(f"{which}.md 要的名字在 rules／theme／{house} 裡沒有："
                           f"{missing}")
    return (rules_module.fill(body, house) + "\n\n---\n\n" + as_text(name))
