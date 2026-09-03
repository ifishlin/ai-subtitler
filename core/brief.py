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

import re
from pathlib import Path
from typing import Any

from core import passages as passages_module
from core import rules as rules_module

ROOT = Path(__file__).resolve().parent.parent

# 送進 prompt 的逐字稿和照片說明，一段／一張最多幾個字。兩個都曾經是寫死的
# 小數字（88 和 70），而那不是設計，是「先讓 prompt 短一點」隨手打的。
PASSAGE_CHARS = rules_module.at("brief.passage_chars", 200)
CAPTION_CHARS = rules_module.at("brief.caption_chars", 200)

# 畫格上偶爾帶著同一支新聞快報下一條的死傷數字（快報一支影片講好幾件事，
# 抓格是按秒數切的，不看內容）。擋的是這種「剛好經過」，不是題目真的在講
# 死傷——那種時候 `term`（搜尋詞）裡也會有這些字，見 sheet() 裡的用法。
_DISASTER_WORDS = re.compile(
    r"\b(killed|dead|deaths?|died|dies|dying|injured|crash(?:ed)?|casualt)",
    re.IGNORECASE)


def sheet(name: str) -> dict[str, Any]:
    """Everything available for this topic, in the shape a writer needs it."""
    from core import topic as topic_module
    from core import stock as stock_module
    pile = topic_module.load(name)
    pictures = []
    for item in pile["sources"]["images"]:
        # 畫格是從整支影片抓下來的一格，不是搜來的——一支新聞快報講完
        # 這一題，下一秒可能在講空難死傷。那一格會通過每一道門（程式看不出
        # 畫格上寫什麼），只有讀了 caption 的人才知道那是另一條新聞。
        # `term` 是搜尋詞，不是這張圖真正的內容——如果詞裡本來就有這些字，
        # 代表是真的為了這件事去搜死傷數字，不擋；擋的是「剛好經過」那種。
        if (item.get("kind") == "frame"
                and _DISASTER_WORDS.search(item.get("caption", ""))
                and not _DISASTER_WORDS.search(item.get("term", ""))):
            continue
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
            # 攤平成一則一則，而且按讚數排序。存的是分組的形狀
            # `{url, outlet, comments: [...]}`，直接送這一層出去的話，
            # 每一組都沒有 `say`，as_text 印出來就是三行「- 」。
            # 讚數排序是因為要的是「哪一句話讓很多人點頭」，不是前二十則。
            # 帶網址的丟掉：那是機器人和自我推銷，不是觀眾的話。濾在這裡
            # 而不是在 `voices_of()` 裡 —— 那一支要忠實，`voice_count()` 數的
            # 是「收到幾則」，不是「幾則好用」，兩個數字不該互相牽動。
            "voices": sorted(
                (one for one in topic_module.voices_of(pile)
                 if "http" not in one["say"]),
                key=lambda one: -one["likes"])[:20],
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
            # 存好的那一份，不現算。這裡和 `writer.fasten()` 各算一次的時候，
            # 中間只要有人補了一篇報導，關鍵詞就變、排序就變，而模型回答裡的
            # `C3` 會指到另一段 —— 兩邊各自都正確地算了一次，沒有人會報錯。
            "passages": passages_module.stored(name),
            "stock": stock_offer(name)}


def stock_offer(name: str, fresh: bool = False) -> list[dict[str, Any]]:
    """這個題目這一次拿到哪幾支情境影片。抽樣和保存都在 `broll.offer()`。"""
    from core import broll as broll_module
    return broll_module.offer(
        name, per=int(rules_module.at("collect.stock_per_group", 3)),
        fresh=fresh)


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
    out.update({f"V{index}": one["file"]
                for index, one in enumerate(found["stock"], start=1)})
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
        out.append("  下面「影片段落」清單裡這幾支的段落還是會出現——"
                    "切點是照聲音的停頓算的，跟字幕比對是兩件事，段落本身"
                    "可能還是切得乾淨。只是這支影片沒辦法自動判斷「這段適不"
                    "適合這句話」，那個判斷落回你身上，多讀一遍那段引言再決定。")
        out.append("")

    # 按影片分組，每一組掛它自己的標題。
    #
    # 本來每一行只寫媒體名，而同一家常常有兩支完全不同的影片：PBS 的
    # 〈News Wrap〉是主播在攝影棚念稿，〈WATCH: Trump signs order〉是橢圓形
    # 辦公室的原始畫面；CNN 的〈Enten says opinion is clear〉是有人站在圖表
    # 前面。畫面差得很遠，而 prompt 上兩者都只寫「PBS NewsHour」。
    #
    # 以前靠底下那行 `file:` 路徑勉強分得出來，而那一行今天被拿掉了 ——
    # 拿掉是對的（檔名會誘發寫檔名），但拿掉資訊要補回去。標題本來就在這筆
    # 資料裡，從來沒被用過，而且它比檔名有用：`xxx.mp4` 說不出畫面長什麼樣，
    # 〈WATCH: Trump signs order〉說得出。
    #
    # 分組而不是每行掛標題：五支影片五行，比十五行便宜。
    out.append("## 影片段落　—— 起訖只能從這裡挑，它們已經落在句子邊界上")
    seen_film = None
    for index, one in enumerate(found["passages"], start=1):
        which = (one.get("file"), one.get("title"))
        if which != seen_film:
            seen_film = which
            out.append("")
            out.append(f"{one['outlet']}〈{(one.get('title') or '')[:52]}〉")
        out.append(f"[C{index}] {one['start']}–{one['end']}s（{one['seconds']}s）")
        # 整段給，不截。本來寫死 88 字，而四十段裡有三十一段比那長 ——
        # 「this right, as you said, Ted Cruz keeps that he is renaming Lake
        # Ontario to be Lake Amer」斷在那裡，後半句「which is something that
        # he has been threatening for days」才是那一段真正的內容。
        # 挑段落是判斷「這段適不適合這句話」，而半句話判斷不了。
        out.append(f"      {one['said'][:PASSAGE_CHARS]}")
    out.append("")

    # 舊的標題寫「兩行不一致就是選錯了」，而那句話是錯的：
    # 「Server room of BalticServers」對「data center」字面上完全不一致，
    # 圖卻完全正確。caption 是拿來讀的，不是拿來比對的。
    # 一張圖一行。之前是四行：編號＋種類＋term、caption、（畫格才有的）
    # 第幾秒、file 路徑。四行乘以六十四張吃掉整份 prompt 的六成，而其中三行
    # 沒有人在讀：
    #
    #   file      `fasten()` 拿 P18 去 `pick()` 查，prompt 裡那行沒人用，
    #             而 script.md 明寫「寫檔名一定會錯」—— 送了等於在邀請
    #   term      我們搜什麼，不是圖是什麼。標題自己都寫著「照 caption 判斷」，
    #             叫模型忽略的東西就不該送。而且重複得厲害（data center ×3）
    #   種類      真實／示意／新聞畫格，沒有任何門在讀 —— `is_real()` 只分
    #             「自製」和「不是自製」，而這裡每一張都不是自製；credit 由
    #             `build()` 從紀錄燒，不靠模型寫的字
    #
    # 留下來的只有兩樣：圖自己的說明，以及畫格的「誰在第幾秒說了什麼」——
    # 那是唯一指得回事件本身的一種。
    # 編號一定是這張圖在 found["pictures"] 裡原本的位置，不是分區之後重新
    # 數的——`pick()` 也是照這份清單原本的順序查 P#，兩邊分家的話編號就
    # 對不起來。所以下面分兩輪印，但共用同一份 enumerate。
    numbered = list(enumerate(found["pictures"], start=1))

    def picture_line(index: int, one: dict[str, Any]) -> None:
        # 說明也整句給。本來 100，我今天為了省 prompt 改成 70 —— 只算了
        # 省多少字，沒算三十五張裡有二十四張會被截。上限是為了擋住偶爾出現的
        # 三百字長說明，不是為了省空間。
        said = one["caption"][:CAPTION_CHARS] or "（來源沒有寫說明，這張圖的內容不明"
        if not one["caption"].strip() and one["term"]:
            # 沒有說明的時候，搜尋詞是僅剩的線索。只有這時候才給 ——
            # 它是查錯用的來源資訊，不是圖的內容。
            said = f"（沒有說明。我們搜的是：{one['term'][:40]}）"
        elif not one["caption"].strip():
            said += "）"
        out.append(f"[P{index}] {said}")
        if one.get("at") is not None:
            out.append(f"      {one['outlet']} 第 {one['at']:.0f} 秒"
                       f"：{one['said'][:CAPTION_CHARS]}")

    out.append("## 照片　—— 這是來源自己對每張圖的說明。挑的時候照這句判斷")
    for index, one in numbered:
        if one.get("at") is None:
            picture_line(index, one)
    out.append("")

    # 畫格：從影片抓下來的一格，不是拍來當照片用的——一定帶著台標、下標，
    # 而且底下那行是「那一秒在說什麼」，不是圖上有什麼。以前這兩種混在同一
    # 節底下、靠有沒有「第 N 秒」那一行才分得出來，`visual.md` 卻寫著
    # 「素材表上標『畫格』的那些」——但從來沒有任何一張真的標著這兩個字。
    # 現在分成自己的一節，講的話跟看到的字對得上。
    frames = [(index, one) for index, one in numbered if one.get("at") is not None]
    if frames:
        out.append("## 新聞畫格　—— 從影片抓的一格，底下那行是那一秒在說什麼，"
                    "不是圖上有什麼。不能當卡片背景，滿版鋪開會把台標、下標裁掉")
        for index, one in frames:
            picture_line(index, one)
        out.append("")

    # 欄位名是 `say`。這裡本來寫 `text` —— 而 `.get("text")` 不會拋錯，只會
    # 回空字串，所以六十則留言變成十二行「- 」，八個字。同一個欄位名這個專案
    # 已經寫錯三次，所以現在只有 `topic.voices_of()` 一個讀法。
    #
    # 讚數帶著送：一則被按四十次讚的話，跟一則沒人理的話，對「觀眾在意什麼」
    # 的意義完全不同，而那正是這一節存在的理由。
    out.append("## 鄉民反應　—— 按讚數排的，拿來判斷這件事在觀眾那裡是什麼溫度，"
                "不是拿來抄語氣。按讚數高的常常是最狠的那句，狠不等於好笑——"
                "「詼諧要來自比喻，不是來自嘲笑」優先。留言在講「這件事在哪裡"
                "碰到我」的時候特別有用，那種通常讚數不高，要自己往下找；"
                "整批都在表態、沒有一條在講切身的，就是這批留言對你沒用，"
                "回去看事實清單")
    for voice in found["voices"][:12]:
        said = " ".join(voice["say"].split())[:90]
        out.append(f"- {said}　（{voice['likes']} 讚）")

    # 情境影片。跟照片一樣只有一行說明可以判斷，但它會動 —— 所以它的用途
    # 是「這一句需要畫面在動，而手上的新聞片段都不對」，不是拿來當證據。
    if found.get("stock"):
        out += ["", "## 情境影片　—— 會動，但**不是這件事的畫面**。"
                    "不能拿來當證據，只能拿來讓畫面別停住"]
        for index, one in enumerate(found["stock"], start=1):
            out.append(f"[V{index}] {one['group']}　{one['term']}"
                       f"　（{one['seconds']}s）")

    # 卡片背景的關鍵字，跨題目共用，不屬於這個題目——所以不編號，只列出
    # 字串。想要「鈔票特寫」這種畫面，先看這裡有沒有已經寫過的關鍵字夠
    # 貼近，貼近就照抄那個字串；真的沒有再自己想一個新的。
    from core import backgrounds as backgrounds_module
    reused = backgrounds_module.top_keywords()
    if reused:
        out += ["", "## 卡片背景已經有的關鍵字　—— 想要類似的畫面就照抄，"
                    "不要多想一個意思一樣的新詞"]
        out.append("、".join(reused))
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
    # `visual.md` 也要送。script.md 第八行寫著「畫面怎麼配、卡片怎麼畫，在
    # visual.md」—— 而那個檔案**從來沒有被放進 prompt 裡**。模型讀到的是一句
    # 指向它打不開的檔案的話，然後只好自己編卡片的欄位。
    #
    # `card_wrong` 那道門就是為此存在的：`bars` 的長度被寫成「超過 1/3」，
    # 八道門過了，四分鐘後死在 ImageDraw。當時記成「模型亂寫欄位」，
    # 實際上是沒有人告訴過它欄位長什麼樣。
    out = []
    for part in (f"{which}.md", "visual.md"):
        body = (ROOT / "assets" / "prompts" / part).read_text(encoding="utf-8")
        missing = rules_module.unfilled(body, house)
        if missing:
            raise RuntimeError(f"{part} 要的名字在 rules／theme／{house} 裡沒有："
                               f"{missing}")
        out.append(rules_module.fill(body, house))
    return "\n\n---\n\n".join(out) + "\n\n---\n\n" + as_text(name)
