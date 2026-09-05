"""把素材讀成一句一句帶出處的事實。

這一步本來不存在。收集把標題和檔案抓回來，寫作的 prompt 有一節「## 事實
（每一條都要指得回出處）」，而中間沒有任何東西把內容讀出來 —— 那一節永遠是
空的，除非有人手打。

前兩支成片（好萊塢 17 條、萊比錫 16 條）的事實是我讀字幕整理的，而我當時
沒發現那不是流程的一部分。結果是 `docs/TESTED.md` 把第 ⑤ 步記成 ✅，
記的其實是「我寫的文案過了門」，不是「這一步會自己完成」。

三個模型（30B、32B、117B）在同一個題目上都只寫出四到八句而不是三十三句，
因為它們手上只有二十三個標題。不是能力問題，是**沒有東西可寫**。

## 兩種素材，兩種讀法

```
影片   字幕      出處是「CNN 第 104 秒」      回得去看那一刻
報導   正文      出處是「CNBC〈標題〉」        回得去看那一篇
```

一開始只讀字幕，因為報導從 Google News RSS 回來只有標題和一個轉址。
後來 `core/wiring.py` 那一頁把結果算出來：**收了二十三篇，一個字都沒有進到
prompt 裡** —— 報導能擋你（篇數、平衡檢查），不能幫你。所以 `core/article.py`
去把正文抓回來，這裡多了一支 `ask_report()`。

報導比影片重要的地方在**立場**：二十三篇的分布是左 6、中立 16、右 5，而影片
只有五支。事實全部來自五支影片的時候，同一件事會重複四次而不會互相矛盾，
於是 `script.md` 要的「找出他們彼此矛盾、或都沒提的地方」沒有材料可用。

## 一支一支問

不管字幕還是正文，都是一個來源一次呼叫。混在一起問，模型會把 CNN 說的話掛
在 Reuters 名下 —— 而**假出處沒有任何門攔得住**，成品上就是一行看起來完全
合理的字。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

from core import rules as rules_module

# 一次讀多少字幕。整支影片的字幕動輒六千字，五支就三萬字 —— 超過模型讀得完
# 的量，而且長了它就開始跳著讀。一支一支問，答案短、可歸屬、可重試。
MOST_CHARS = rules_module.at("facts.most_chars", 6000)
MOST_PER_VIDEO = rules_module.at("facts.most_per_video", 6)
# 報導可以多一點：一篇文章比一支新聞影片講得完整，而且報導的價值在立場，
# 一家的說法常常要兩三條才說得清楚。
MOST_PER_REPORT = rules_module.at("facts.most_per_report", 5)
# 每條事實附的白話說明上限，以及要不要問這一句——見 assets/rules.json 的
# gist_why。關掉的話，這裡就不問，也不會有東西可以讓 fasten() 去掛。
GIST_CHARS = rules_module.at("facts.gist_chars", 40)
GIST_ENABLED = rules_module.at("facts.gist_enabled", True)


def captions_of(video: dict[str, Any]) -> Path | None:
    """這支影片的字幕檔。

    先看紀錄裡的 `captions` 欄，找不到就照影片檔名去磁碟上找。兩條路是因為
    紀錄會跟磁碟不一致：一次裁決把二十九支的紀錄刪掉，檔案留在原地，而留下
    的那幾支的 `captions` 欄從來沒被填。以磁碟為準的那條路救得回來。
    """
    named = video.get("captions")
    if named and (ROOT / named).is_file():
        return ROOT / named
    where = video.get("file")
    if not where:
        return None
    film = ROOT / where
    found = sorted(film.parent.glob(f"{film.stem}*.vtt"))
    return found[0] if found else None


def said_in(video: dict[str, Any]) -> str:
    """這支影片說了什麼，一整段連續的文字。

    時間點留在裡面（每隔幾句插一個），因為出處要指得回「第 66 秒」——
    那是可以回去看的東西，而「The Economist」不是。
    """
    from core import captions as captions_module
    where = captions_of(video)
    if not where:
        return ""
    cues = captions_module.read(where)
    out, mark = [], -99.0
    for cue in cues:
        if cue["start"] - mark >= 20:
            out.append(f"[{int(cue['start'])}s]")
            mark = cue["start"]
        out.append(cue["text"])
    return " ".join(out)[:MOST_CHARS]


def in_traditional(words: str) -> str:
    """把簡體字換成繁體，**一個字一個字換**。

    prompt 已經要求繁體，而二十條裡還是有一條夾著「经济」。要求是機率，換字
    是必然 —— 能用程式做完的事不要留給模型。

    ## 為什麼不整句丟給 opencc

    第一版就是那樣寫的，而它**弄壞了本來正確的繁體**：

    ```
    進去   電價的上漲並非由需求引起，而是由供應側的限制造成
    出來   ……由供應側的限製造成
    ```

    opencc 的詞彙規則看到「制造」就換成「製造」，不管那個「制」屬於前面的
    「限制」。s2t、s2tw、s2twp 三個都一樣，而且**對已經是繁體的輸入也會做**。

    所以改成逐字：先用 `script._is_simplified()` 問這個字是不是簡體寫法，
    只有是的才換，而且單獨換一個字 —— 一個字沒有上下文，詞彙規則咬不到。
    判斷簡體用的是既有那一支，`simplified` 門和這裡叫同一個函式，不會有
    兩套說法。

    這一步在這裡而不是在文案那一關：`simplified` 門擋得住簡體的台詞，但它
    退回的是整份文案，而錯是在事實進來的時候發生的。
    """
    try:
        import opencc                         # noqa: F401
    except ImportError:                       # 沒裝就原樣過，門還在後面
        return words
    from core import script as script_module
    global _CONVERT
    if _CONVERT is None:
        _CONVERT = opencc.OpenCC("s2t")
    return "".join(_CONVERT.convert(char)
                   if script_module._is_simplified(char) else char
                   for char in words)


_CONVERT = None


def source_line(said: str, outlet: str) -> str:
    """出處寫成一種樣子：「CNN 第 104 秒」。

    模型交回來的有「CNN 第0s」、「Fox News 123s」、「Bloomberg 20s」三種，
    還有八條只有秒數。看得懂不等於一致 —— 這一欄會燒到畫面上，而畫面上
    三種格式並排就是沒有人在管的樣子。
    """
    import re
    seconds = re.search(r"(\d+)", said or "")
    when = f"第 {seconds.group(1)} 秒" if seconds else ""
    # 媒體名可能已經在裡面，也可能整條只有秒數。以 outlet 為準重寫一次，
    # 這樣不管模型寫成什麼樣，出來都一樣。
    return f"{outlet} {when}".strip() if outlet else (when or said)


def video_prompt(topic: str, video: dict[str, Any]) -> str | None:
    """The exact text `ask_one()` would send, without spending a model call.

    Split out so a preview button on the topic page can show what's about
    to be asked before anyone asks it -- there was no way to see this short
    of reading the source, which is the same gap `/prompt` already closed
    for the script-writing side. One function decides the shape of the
    question either way, so the preview cannot show something different
    from what actually gets sent.

    Returns `None` when there is nothing to ask about (no captions, or too
    short to be more than a title) -- the caller decides what that means.
    """
    words = said_in(video)
    if len(words) < 200:            # 沒有字幕，或短到不成句
        return None
    outlet = video.get("outlet", "")
    language = rules_module.at("language.name", "繁體中文（台灣用語）")
    gist_line = (
        f"- `gist`：這件事對觀眾來說「所以呢」是什麼，白話講一句，"
        f"最多 {GIST_CHARS} 字。不是重複 `say`，是說它為什麼值得放進片子裡\n"
    ) if GIST_ENABLED else ""
    gist_field = ', "gist": "…"' if GIST_ENABLED else ""
    return (
        f"題目：{topic}\n"
        f"這是 {outlet} 的一支影片，底下是它的字幕。"
        f"[123s] 是那句話出現的秒數。\n\n"
        f"{words}\n\n"
        f"**這支影片講了哪幾件跟題目有關的事？**\n\n"
        f"一條一句話，最多 {MOST_PER_VIDEO} 條。要求：\n"
        # 字幕是英文，事實是要寫進中文文案的。第一版沒說，於是 CNN 那五條
        # 原封不動是英文，另外八條是簡體 —— 兩種都要在這裡就對，不能留給
        # 文案階段的 simplified 門，那時候已經是別人的錯了。
        f"- **一律用{language}**。字幕是英文就翻譯，不要照抄，一個簡體字都不行\n"
        "- 只寫字幕裡真的說過的。**不要補你知道的事**，也不要推論\n"
        "- 有數字就把數字寫進去\n"
        "- 跟題目無關的段落跳過，寧可少寫\n"
        # 第一版寫「一律寫「{outlet} 第 N 秒」」，模型照抄了秒數卻漏掉媒體名，
        # 八條的出處變成「12s」—— 指不回任何人。範例給滿，不要只給規則。
        f'- `from` 照這個樣子寫：「{outlet} 第 87 秒」。'
        f"媒體名一定要在，只寫秒數指不回任何人\n"
        f"{gist_line}\n"
        '只輸出 JSON：{"facts": [{"say": "…", "from": "…"' + gist_field + '}]}')


def ask_one(topic: str, video: dict[str, Any], say=None,
            label: str | None = None) -> list[dict[str, str]]:
    """問一支影片說了哪幾件事。

    一支一支問而不是全部一起，因為出處要精確：模型看著一家媒體的字幕時，
    寫出來的 `from` 只可能是那一家。全部混在一起問，它會把 CNN 說的話
    掛在 Reuters 名下 —— 而那種錯沒有任何門攔得住，成品上就是一行假的出處。

    `label` 是這一支影片存進事實時要用的出處字串，跟問模型時說的 `outlet`
    是兩件事：模型只需要知道自己在讀哪一台的字幕，不需要知道這個題目底下
    這一台是不是還收了別支——那是 `gather()` 手上的資訊，模型看不到。
    `label` 留白就照舊用 `outlet`，只有一個媒體收了不只一支影片，
    `gather()` 才會傳一個能區分開來的字串進來（見那邊的說明）。
    """
    from core import writer as writer_module
    asking = video_prompt(topic, video)
    if asking is None:
        return []
    outlet = video.get("outlet", "")
    shown = label or outlet
    if say:
        say(0, 1, f"讀 {outlet} 的字幕")
    said, _ = writer_module.ask(asking, None)
    got = writer_module.read(said)
    out = []
    for one in (got.get("facts") or [])[:MOST_PER_VIDEO]:
        if not isinstance(one, dict):
            continue
        words_said = str(one.get("say") or "").strip()
        if not words_said:
            continue
        # 出處這一半從頭到尾是程式決定的，不是模型寫的——source_line() 只
        # 從模型的答案裡抽數字，媒體名（或這裡的 shown）是呼叫端給的，模型
        # 寫什麼都會被蓋掉。所以能不能分清楚是哪一支，取決於這裡傳的是
        # `outlet` 還是 `shown`，跟模型答得準不準無關。
        fact = {"say": in_traditional(words_said),
                "from": source_line(str(one.get("from") or ""), shown)}
        if GIST_ENABLED:
            fact["gist"] = in_traditional(str(one.get("gist") or "")
                                          .strip())[:GIST_CHARS]
        out.append(fact)
    return out


_SAYS = "指出|認為|報導|表示|寫道|提到|說"


def without_self_attribution(said: str, outlet: str, where: str) -> str:
    """把句首「CNBC指出，」這種自我指涉剝掉。

    出處已經有一整欄了，句子裡再寫一次是重複；而模型有時候會把整個
    「CNBC〈AI data center 'frenzy' 〉指出，」搬到句首，那一串在畫面上就是噪音。

    只剝**這一篇自己的**媒體名。「SemiAnalysis認為，」、「Counterpoint
    Research 的研究員 Marc Einstein 認為，」要留著 —— 那是文章裡引述的別人，
    也正是報導最有價值的部分（誰在反駁誰、誰的算法不同）。剝掉它們會把立場
    變成沒有主人的斷言。
    """
    import re
    for head in (where, outlet):
        if not head:
            continue
        pattern = rf"^{re.escape(head)}\s*(?:{_SAYS})?\s*[，,：:]\s*"
        trimmed = re.sub(pattern, "", said)
        if trimmed != said and trimmed:
            return trimmed
    return said


def report_prompt(topic: str, report: dict[str, Any]) -> tuple[str | None, str]:
    """The exact text `ask_report()` would send, without spending a model
    call. See `video_prompt()` -- same reason, same split.

    Returns (text, why) -- `text` is `None` when the article's body could
    not be fetched, and `why` says what happened (paywall, bot wall, ...).
    """
    from core import article as article_module

    words, why, _ = article_module.text_of(topic, report)
    if not words:
        return None, why

    outlet = report.get("outlet", "")
    language = rules_module.at("language.name", "繁體中文（台灣用語）")
    gist_line = (
        f"- `gist`：這件事對觀眾來說「所以呢」是什麼，白話講一句，"
        f"最多 {GIST_CHARS} 字。不是重複 `say`，是說它為什麼值得放進片子裡\n"
    ) if GIST_ENABLED else ""
    gist_field = ', "gist": "…"' if GIST_ENABLED else ""
    text = (
        f"題目：{topic}\n"
        f"這是 {outlet} 的一篇報導，標題是〈{report.get('title', '')}〉。\n\n"
        f"{words}\n\n"
        f"**這篇報導講了哪幾件跟題目有關的事？**\n\n"
        f"一條一句話，最多 {MOST_PER_REPORT} 條。要求：\n"
        f"- **一律用{language}**。原文是英文就翻譯，不要照抄，一個簡體字都不行\n"
        "- 只寫這篇文章裡真的寫過的。**不要補你知道的事**，也不要推論\n"
        "- 有數字就把數字寫進去\n"
        "- 這一家的**立場和說法**比事件本身有用："
        "如果它跟別人算法不同、或它在反駁什麼，那一條要寫出來\n"
        "- 跟題目無關的段落跳過，寧可少寫\n"
        f'- `from` 一律寫「{outlet}〈{str(report.get("title") or "")[:24]}〉」\n'
        f"{gist_line}\n"
        '只輸出 JSON：{"facts": [{"say": "…", "from": "…"' + gist_field + '}]}')
    return text, ""


def ask_report(topic: str, report: dict[str, Any],
               say=None) -> tuple[list[dict[str, str]], str]:
    """問一篇報導說了哪幾件事。回傳（事實, 抓不到的原因）。

    跟 `ask_one()` 幾乎一樣，差別只在兩件事：素材是正文而不是字幕，出處寫成
    「CNBC〈標題〉」而不是「CNBC 第 104 秒」—— 一篇文章沒有秒數，硬編一個
    出來就是假的，而假出處沒有任何門攔得住。

    報導比影片重要的地方在**立場**。二十三篇的分布是左 6、中立 16、右 5，
    而影片只有五支 —— `script.md` 要的「找出他們彼此矛盾的地方」只有在這裡
    才有材料。事實全部來自五支影片的時候，同一件事會重複四次，不會矛盾。
    """
    from core import writer as writer_module

    asking, why = report_prompt(topic, report)
    if asking is None:
        return [], why

    outlet = report.get("outlet", "")
    title = str(report.get("title") or "")[:24]
    where = f"{outlet}〈{title}〉" if title else outlet
    if say:
        say(0, 1, f"讀 {outlet} 的報導")
    said, _ = writer_module.ask(asking, None)
    got = writer_module.read(said)

    out = []
    for one in (got.get("facts") or [])[:MOST_PER_REPORT]:
        if not isinstance(one, dict):
            continue
        words_said = str(one.get("say") or "").strip()
        if not words_said:
            continue
        # 出處一律用程式組的那一份，不用模型回的。它會把標題抄錯、抄長、或
        # 只寫媒體名 —— 而這一欄要指得回一篇真的文章。
        fact = {"say": without_self_attribution(
            in_traditional(words_said), outlet, where), "from": where}
        if GIST_ENABLED:
            fact["gist"] = in_traditional(str(one.get("gist") or "")
                                          .strip())[:GIST_CHARS]
        out.append(fact)
    return out, ""


def unread(pile: dict[str, Any]) -> list[str]:
    """還沒有人讀過的來源，用媒體名說出來。

    這一支存在，是因為那顆「整理事實」按鈕本來的顯示條件是「事實少於八條」。
    四個題目都有四十九到一百二十五條，所以按鈕**在每一個題目上都是藏起來的**
    —— 而裁決之後補下載回來的影片、後來才收的報導，就永遠沒有人讀。

    藏起來又不自動做，是最糟的組合：畫面上「整理過了」和「整理過，但後來
    又進了五支影片」長得一模一樣。
    """
    from core import topic as topic_module
    # 這個章是後加的，而在它之前整理過的題目一個章都沒有 —— 照字面判就會說
    # 四十八個來源沒讀過，其實全部讀過了。分辨得出來：**一個章都沒有、而事實
    # 已經夠多**，那是舊的題目，不是沒讀過的題目。下一次重跑就會蓋上章。
    #
    # 只認「一個都沒有」這種全有全無的狀態。有一個章就表示這個題目已經在新
    # 制度裡了，那時候沒有章就真的是沒讀過。
    kinds = ("videos", "reports")
    if not any(one.get("read") for kind in kinds
               for one in pile.get("sources", {}).get(kind) or []):
        if len(topic_module.facts_of(pile)) >= rules_module.at("facts.least", 8):
            return []
    out = []
    for one in topic_module.settled(pile, "videos"):
        if one.get("file") and captions_of(one) and not one.get("read"):
            out.append(one.get("outlet", "") or "一支影片")
    for one in topic_module.settled(pile, "reports"):
        if not one.get("read"):
            out.append(one.get("outlet", "") or "一篇報導")
    return out


def source_codes(items: list[tuple[str, str]], letter: str = "S") -> dict[str, str]:
    """A globally unique code for every distinct url in `items` -- a primary
    key, one per video (or report), never shared and never repeated. `S1`
    names one specific video, full stop; it does not need the outlet name
    standing next to it to mean anything.

    An earlier version numbered within each outlet instead, so "PBS(S1)" and
    "CNN(S1)" were two different videos sharing one label -- the exact
    ambiguity this function exists to remove, reintroduced one level up.
    A code that needs a second piece of information before it identifies
    one thing is not a primary key.

    Three callers need this, not one: `gather()` here, for a fact's `from`;
    `brief.prompt()`, for a news-frame's "PBS NewsHour 第 6 秒" line, same
    ambiguity, same reason (a frame's `outlet` field does not say which of
    that outlet's videos it was cut from, even though the frame record
    itself already knows -- it just wasn't being read); and the topic page's
    own video/report list, which wants a code on every row to point at. One
    function for all three, so the fix does not drift into slightly
    different answers to the same question.

    Whether a *citation sentence* shows the code at all is a separate
    question from whether the code exists -- see `collides()`: a solitary
    video for its outlet keeps reading as plain "PBS NewsHour 第 87 秒",
    because a code that never disambiguates anything there is just clutter --
    even though that video still has a real, unique `S#` if anything else
    needs to point at it.

    `letter` picks the prefix: `S`（影片來源）for videos, `R`（報導）for
    reports. `P`（照片）、`C`（片段）、`F`（事實）都被佔走了，`V` 也是
    （情境影片的池子），不然「V3」在這個題目裡會有兩種意思。

    The code is a function of the url itself, sorted, not of position in
    whatever list happens to be passed in -- position drifts if the topic is
    re-collected or edited; the url of an already-downloaded item does not.
    So the same item gets the same code every time this is called with the
    same set of items, and nothing needs to be stored anywhere to remember
    which code meant which item: re-run this function with the current list
    and it reconstructs the same mapping. Call it again (or write a one-line
    script that does) whenever a code like "S7" needs to be traced back to
    an actual title and URL.
    """
    urls = sorted({url for _, url in items})
    return {url: f"{letter}{index}" for index, url in enumerate(urls, start=1)}


def collides(items: list[tuple[str, str]]) -> set[str]:
    """Which outlets, among `items`, have more than one distinct url.

    For deciding whether a citation sentence is worth showing a code in --
    see `source_codes()`. The code itself is always unique; this just says
    where showing it earns its keep.
    """
    from collections import defaultdict
    by_outlet: dict[str, set[str]] = defaultdict(set)
    for outlet, url in items:
        by_outlet[outlet].add(url)
    return {outlet for outlet, urls in by_outlet.items() if len(urls) > 1}


def gather(name: str, say=None) -> dict[str, Any]:
    """讀完影片的字幕和報導的正文，把事實加進去。

    加而不是換：既有的事實可能是人寫的，而人寫的那幾條通常是最重要的。
    重複用 `say` 比對 —— 同一句話兩家都說過的時候，留先來的那一條。

    影片和報導在同一支函式裡，因為對用的人來說那是一件事：**把素材讀成
    可以寫的東西**。分成兩顆按鈕就變成他要記得兩件事都要按，而這個專案
    違反過的規則全部是「要記得去做某個動作」那一類。

    破壞性的動作放最後：全部讀完、收齊、才寫進紀錄。中途失敗只損失時間。
    """
    from core import topic as topic_module
    pile = topic_module.load(name)
    have = {one["say"] for one in topic_module.facts_of(pile)}
    fresh, asked, empty = [], 0, []
    # 真的問到模型、拿到回答的那些來源。用網址認人，因為那是來源唯一穩定的
    # 識別 —— 檔名會變，媒體名會重複（PBS 那題有兩支）。
    read_from: set[str] = set()

    videos = [one for one in topic_module.settled(pile, "videos")
              if captions_of(one)]
    reports = topic_module.settled(pile, "reports")
    steps = len(videos) + len(reports)
    step = 0

    # 媒體名會重複（PBS 那題收了兩支），而 source_line() 存的出處只有
    # 「{outlet} 第 X 秒」——兩支各自的「第 0 秒」存進事實清單之後就分不開
    # 了，指不回是哪一支。每支影片都有自己唯一的代碼（source_codes()），
    # 但只有撞名的媒體才在句子裡標出來，只收一支的維持現在乾淨的
    # 「PBS NewsHour 第 87 秒」，不用每支都變複雜（collides()）。
    video_pairs = [(v.get("outlet", ""), str(v.get("url") or "")) for v in videos]
    codes = source_codes(video_pairs)
    doubled = collides(video_pairs)

    for video in videos:
        step += 1
        outlet = video.get("outlet", "")
        if say:
            say(step, steps, f"讀 {outlet} 的字幕")
        code = codes.get(str(video.get("url") or ""))
        label = f"[{code}] {outlet}" if outlet in doubled else None
        try:
            got = ask_one(name, video, None, label)
        except Exception as error:                                # noqa: BLE001
            # 問不到模型跟「這支沒說什麼」是兩件事。前者要出聲，後者不必。
            raise RuntimeError(f"問不到模型：{error}") from error
        asked += 1
        read_from.add(str(video.get("url") or ""))
        if not got:
            empty.append(outlet)
        for one in got:
            if one["say"] not in have:
                have.add(one["say"])
                fresh.append(one)

    # 報導。抓不到正文是常態（付費牆、擋機器人），所以它不讓整輪停下來 ——
    # 但每一篇為什麼抓不到都要說出來，而且要寫進檔案。上一次那句 ⚠ 只活在
    # 記憶體裡，重啟伺服器就沒了。
    unread, read_ok = [], 0
    for report in reports:
        step += 1
        if say:
            say(step, steps, f"讀 {report.get('outlet', '')} 的報導")
        try:
            got, why = ask_report(name, report, None)
        except Exception as error:                                # noqa: BLE001
            raise RuntimeError(f"問不到模型：{error}") from error
        if why:
            unread.append(f"{report.get('outlet', '')}：{why}")
            continue
        read_ok += 1
        asked += 1
        read_from.add(str(report.get("url") or ""))
        if not got:
            empty.append(f"{report.get('outlet', '')}（報導）")
        for one in got:
            if one["say"] not in have:
                have.add(one["say"])
                fresh.append(one)

    pile = topic_module.load(name)
    pile["facts"] = topic_module.facts_of(pile) + fresh
    # 讀過的蓋一個章。少了它，「還沒整理過」和「整理過但後來又收了五支影片」
    # 在畫面上一模一樣 —— 而那顆按鈕本來的顯示條件是「事實少於八條」，於是
    # 四個題目都有五十條以上，按鈕全部藏起來，補下載回來的影片永遠沒人讀。
    stamp = int(time.time())
    for kind in ("videos", "reports"):
        for one in pile.get("sources", {}).get(kind) or []:
            if one.get("url") in read_from:
                one["read"] = stamp
    # 警告寫進檔案，不只寫進工作日誌。看得到的地方才算報告過。
    trouble = list((pile.get("gathered") or {}).get("trouble") or [])
    trouble = [one for one in trouble if not one.startswith("讀不到正文")]
    if unread:
        trouble.append(f"讀不到正文 {len(unread)} 篇：{'；'.join(unread)}")
    if reports and not read_ok:
        trouble.append(f"{len(reports)} 篇報導一篇都讀不到正文")
    pile.setdefault("gathered", {})["trouble"] = trouble
    topic_module.save(name, pile)

    return {"asked": asked, "added": len(fresh),
            "total": len(pile["facts"]),
            "videos_read": len(videos),
            "reports_read": read_ok,
            # 讀了 N 個、拿回 0 條要說出來。這個專案最貴的失敗就是靜靜回報零。
            "nothing_from": empty,
            "without_text": unread,
            "without_captions": [one.get("outlet", "") for one
                                 in topic_module.settled(pile, "videos")
                                 if not captions_of(one)]}
