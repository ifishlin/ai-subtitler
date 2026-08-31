"""把素材讀成一句一句帶出處的事實。

這一步本來不存在。收集把標題和檔案抓回來，寫作的 prompt 有一節「## 事實
（每一條都要指得回出處）」，而中間沒有任何東西把內容讀出來 —— 那一節永遠是
空的，除非有人手打。

前兩支成片（好萊塢 17 條、萊比錫 16 條）的事實是我讀字幕整理的，而我當時
沒發現那不是流程的一部分。結果是 `docs/TESTED.md` 把第 ⑤ 步記成 ✅，
記的其實是「我寫的文案過了門」，不是「這一步會自己完成」。

三個模型（30B、32B、117B）在同一個題目上都只寫出四到八句而不是三十三句，
因為它們手上只有二十三個標題。不是能力問題，是**沒有東西可寫**。

## 為什麼讀字幕，不讀報導內文

報導從 Google News RSS 回來只有標題和一個轉址，沒有內文。影片字幕是這條
路上唯一拿得到的**完整句子**，而且它帶著兩樣寫作需要的東西：說話的是哪一家、
第幾秒說的。所以事實的出處指得回「The Economist 第 66 秒」，而不只是
「The Economist」。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

from core import rules as rules_module

# 一次讀多少字幕。整支影片的字幕動輒六千字，五支就三萬字 —— 超過模型讀得完
# 的量，而且長了它就開始跳著讀。一支一支問，答案短、可歸屬、可重試。
MOST_CHARS = rules_module.at("facts.most_chars", 6000)
MOST_PER_VIDEO = rules_module.at("facts.most_per_video", 6)


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
    """把簡體字換成繁體。

    prompt 已經要求繁體，而二十條裡還是有一條夾著「经济」。要求是機率，換字
    是必然 —— 能用程式做完的事不要留給模型。只換字不換詞（`s2t` 而不是
    `s2twp`），因為詞彙表會動到人名和機構名。

    這一步在這裡而不是在文案那一關：`simplified` 門擋得住簡體的台詞，但它
    退回的是整份文案，而錯是在事實進來的時候發生的。
    """
    try:
        import opencc
    except ImportError:                       # 沒裝就原樣過，門還在後面
        return words
    global _CONVERT
    if _CONVERT is None:
        _CONVERT = opencc.OpenCC("s2t")
    return _CONVERT.convert(words)


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


def ask_one(topic: str, video: dict[str, Any], say=None) -> list[dict[str, str]]:
    """問一支影片說了哪幾件事。

    一支一支問而不是全部一起，因為出處要精確：模型看著一家媒體的字幕時，
    寫出來的 `from` 只可能是那一家。全部混在一起問，它會把 CNN 說的話
    掛在 Reuters 名下 —— 而那種錯沒有任何門攔得住，成品上就是一行假的出處。
    """
    from core import writer as writer_module
    words = said_in(video)
    if len(words) < 200:            # 沒有字幕，或短到不成句
        return []
    outlet = video.get("outlet", "")
    language = rules_module.at("language.name", "繁體中文（台灣用語）")
    asking = (
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
        f"媒體名一定要在，只寫秒數指不回任何人\n\n"
        '只輸出 JSON：{"facts": [{"say": "…", "from": "…"}]}')
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
        out.append({"say": in_traditional(words_said),
                    "from": source_line(str(one.get("from") or ""), outlet)})
    return out


def gather(name: str, say=None) -> dict[str, Any]:
    """讀完這個題目所有有字幕的影片，把事實加進去。

    加而不是換：既有的事實可能是人寫的，而人寫的那幾條通常是最重要的。
    重複用 `say` 比對 —— 同一句話兩家都說過的時候，留先來的那一條。
    """
    from core import topic as topic_module
    pile = topic_module.load(name)
    have = {one["say"] for one in topic_module.facts_of(pile)}
    fresh, asked, empty = [], 0, []

    videos = [one for one in topic_module.settled(pile, "videos")
              if captions_of(one)]
    for index, video in enumerate(videos, start=1):
        if say:
            say(index, len(videos), f"讀 {video.get('outlet', '')} 的字幕")
        try:
            got = ask_one(name, video, None)
        except Exception as error:                                # noqa: BLE001
            # 問不到模型跟「這支沒說什麼」是兩件事。前者要出聲，後者不必。
            raise RuntimeError(f"問不到模型：{error}") from error
        asked += 1
        if not got:
            empty.append(video.get("outlet", ""))
        for one in got:
            if one["say"] not in have:
                have.add(one["say"])
                fresh.append(one)

    pile = topic_module.load(name)
    pile["facts"] = topic_module.facts_of(pile) + fresh
    topic_module.save(name, pile)
    return {"asked": asked, "added": len(fresh),
            "total": len(pile["facts"]),
            # 讀了 N 支、拿回 0 條要說出來。這個專案最貴的失敗就是靜靜回報零。
            "nothing_from": empty,
            "without_captions": [one.get("outlet", "") for one
                                 in topic_module.settled(pile, "videos")
                                 if not captions_of(one)]}
