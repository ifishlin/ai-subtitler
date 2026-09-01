"""一批共用的情境影片。

新聞片段是**這件事的證據** —— 有出處、有時間點、由字幕決定切在哪一秒。
情境影片不是：它是一支「高壓電塔在夕陽下」，跟哪一個題目都沒有關係，
補的是節奏不是事實。所以它是第四種鏡頭，而不是 `clip` 的一種。

**一批共用，不是每題現撈。** 兩個理由：

  看過一次就永遠算看過。`unchecked` 那道門要的是有人真的打開看過，而
  一個固定的池子可以一次看完；每題現撈就是每題重看一遍，而「要記得去做
  的動作」在這個專案裡從來沒有被遵守過。

  省硬碟。四個題目各存一份夕陽電塔，是四份。

分兩步：先抓**候選**（只有縮圖和說明，二十幾 KB），人看過、留下要的，
才下載那幾支的影片檔。抓一支 8MB 只為了知道「不是這個」，一百支就是
八百 MB 的浪費。

分類照「短影音實際需要什麼畫面」，不照題目 —— 照題目分的池子，換一個
題目就整個不能用。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core import stock as stock_module

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "assets" / "broll"
BOOK = HERE / "library.json"


# 每一組的搜尋詞。英文，因為 Pexels 的標籤是英文；中文查不到東西。
GROUPS: dict[str, list[str]] = {
    "人與日常": [
        "person walking city street", "family dinner table home",
        "person looking at phone", "people waiting queue",
        "person sitting sofa living room", "hands typing laptop",
    ],
    "錢與帳單": [
        "counting money hands", "paper bills paperwork desk",
        "supermarket checkout scanning", "credit card payment terminal",
        "calculator receipts table",
    ],
    "基礎設施": [
        "power transmission tower sunset", "electrical substation",
        "server room data center", "construction site crane",
        "shipping containers port", "truck highway driving",
        "factory production line",
    ],
    "城市與空景": [
        "city skyline aerial", "traffic time lapse night",
        "empty street morning", "suburban houses aerial",
        "office buildings glass",
    ],
    "自然與天氣": [
        "ocean waves slow motion", "rain on window", "forest trees wind",
        "wildfire smoke", "dry cracked earth drought",
    ],
    "抽象節奏": [
        "clock ticking close up", "gears turning machine",
        "data flowing screen", "paper documents flipping",
        "ink spreading water",
    ],
    "機構與權力": [
        "empty conference room", "courthouse steps exterior",
        "flags waving government building", "signing document pen close up",
        "microphones press conference",
    ],
}


def _load() -> dict[str, Any]:
    if not BOOK.is_file():
        return {"clips": [], "hunted": 0}
    try:
        return json.loads(BOOK.read_text(encoding="utf-8"))
    except Exception:                                             # noqa: BLE001
        return {"clips": [], "hunted": 0}


def _save(book: dict[str, Any]) -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    BOOK.write_text(json.dumps(book, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def library() -> dict[str, Any]:
    """池子現在有什麼。檔案在不在是**每次現看**的，不是存下來的欄位 ——
    存一份就是等它跟硬碟上的實情分岔。"""
    book = _load()
    for one in book["clips"]:
        one["there"] = bool(one.get("file") and (ROOT / one["file"]).is_file())
    return book


def hunt(per_term: int = 3, say=None) -> dict[str, Any]:
    """去 Pexels 找候選。只拿說明和縮圖，不下載影片。

    已經在池子裡的 id 不會重複加，所以這一支可以重跑 —— 加了新的搜尋詞
    之後只會補上新的那幾支，不會把已經看過、已經留下的判斷洗掉。
    """
    book = _load()
    seen = {one["id"] for one in book["clips"]}
    terms = [(group, term) for group, words in GROUPS.items() for term in words]
    added = 0
    quiet = []
    for index, (group, term) in enumerate(terms, start=1):
        if say:
            say(index, len(terms), f"找 {term}")
        try:
            offered = stock_module.search_pexels(term, count=per_term)
        except Exception as error:                                # noqa: BLE001
            quiet.append(f"{term}：{error}")
            continue
        if not offered:
            # 做了 N 次收到 0 筆的步驟要出聲。搜尋詞打錯和「這個詞真的
            # 沒有片子」在畫面上一模一樣，而前者才是要修的那個。
            quiet.append(f"{term}：一支都沒有")
            continue
        for clip in offered:
            if clip.id in seen:
                continue
            seen.add(clip.id)
            added += 1
            book["clips"].append({
                "id": clip.id, "group": group, "term": term,
                "width": clip.width, "height": clip.height,
                "seconds": round(clip.duration, 1),
                "url": clip.url, "page": clip.page,
                "author": clip.author, "still": clip.still,
                # 三態，不是兩態：None 是「還沒看過」，跟「看過而且不要」
                # 不一樣。少了這個分別，一個沒看完的池子看起來會像看完了。
                "keep": None,
                "file": None,
            })
        time.sleep(0.15)
    book["hunted"] = int(time.time())
    book["quiet"] = quiet
    _save(book)
    return {"added": added, "total": len(book["clips"]), "quiet": quiet}


def judge(clip_id: str, keep: bool | None) -> dict[str, Any]:
    """留下、丟掉，或收回判斷。"""
    book = _load()
    for one in book["clips"]:
        if one["id"] == clip_id:
            one["keep"] = keep
            _save(book)
            return one
    raise ValueError(f"池子裡沒有這一支：{clip_id}")


def bring_in(say=None, every: bool = False) -> dict[str, Any]:
    """把片子下載回來。只抓還沒有檔案的，中斷之後再跑一次就是接著抓。

    `every` 是「全部抓下來看」。本來的設計是先看縮圖再決定，但**一張靜圖
    看不出一支影片值不值得留** —— 鏡頭有沒有在動、動得好不好、有沒有晃到
    不能用，全部在那一格裡看不到。所以「省流量」省掉的其實是判斷的依據。

    抓完之後用 `drop_unwanted()` 把不要的掃掉，硬碟就回來了；那一支是分開
    的，因為它是破壞性的，不該夾在下載裡面 —— 抓到一半失敗的時候，不該
    順便把別的東西刪掉。
    """
    book = _load()
    HERE.mkdir(parents=True, exist_ok=True)
    todo = [one for one in book["clips"]
            if (every or one.get("keep"))
            and not (one.get("file") and (ROOT / one["file"]).is_file())]
    got, missed = 0, []
    for index, one in enumerate(todo, start=1):
        if say:
            say(index, len(todo), f"下載 {one['term']}")
        target = HERE / f"{one['id']}.mp4"
        try:
            stock_module.fetch(one["url"], target)
        except Exception as error:                                # noqa: BLE001
            missed.append(f"{one['id']}：{error}")
            continue
        one["file"] = str(target.relative_to(ROOT))
        got += 1
        _save(book)          # 一支一存：中斷了也不會把前面抓好的忘掉
    return {"got": got, "missed": missed, "todo": len(todo)}


def drop_unwanted(also_unseen: bool = False) -> int:
    """掃掉不要的那些檔案。

    預設只掃「看過而且判成不要」的。`also_unseen` 連「還沒看過」的一起掃 ——
    那是清空重來，不是整理，所以要明講。

    破壞性的步驟，所以它是自己一支，不夾在下載那一支裡面 —— 抓到一半失敗
    的時候，不該順便把別的東西刪掉。
    """
    book = _load()
    gone = 0
    for one in book["clips"]:
        if one.get("keep"):
            continue
        # 還沒看過的不掃 —— 掃了就等於替他決定，而他還沒看。
        if one.get("keep") is None and not also_unseen:
            continue
        if one.get("file"):
            here = ROOT / one["file"]
            if here.is_file():
                here.unlink()
                gone += 1
            one["file"] = None
    _save(book)
    return gone


OFFERED = HERE / "offered"


def offer(topic: str, per: int = 3, fresh: bool = False) -> list[dict[str, Any]]:
    """這個題目這一次送出去的是哪幾支。

    抽，不是全部列出去：池子有一百多支，全列會把素材那一節灌成一片清單，
    而模型挑的時候只看得到搜尋詞 —— 一組看三支和看十五支，它挑得一樣好。

    **抽完要存下來。** `sheet()` 被 `as_text()`（印出 V1、V2 給模型看）和
    `fasten()`（把 V2 換回檔案）各呼叫一次；隨機而不存的話，兩邊抽到不同的
    三支，V2 在那兩個地方指著不同的影片 —— 而兩邊各自都「正確地」隨機了，
    不會有任何一邊報錯。這是這個專案的老毛病：同一個事實算兩遍。

    `fresh=True` 是重抽，只有組 prompt 的那一刻會這樣叫。其餘所有人讀的都是
    存下來的那一份。
    """
    import random
    OFFERED.mkdir(parents=True, exist_ok=True)
    note = OFFERED / f"{topic}.json"
    if not fresh and note.is_file():
        try:
            saved = json.loads(note.read_text(encoding="utf-8"))
        except Exception:                                         # noqa: BLE001
            saved = None
        if saved:
            # 存的是 id：池子裡那一支後來被刪掉的話，這裡就要少一支，
            # 而不是回一個指著不存在檔案的紀錄。
            have = {one["id"]: one for one in kept()}
            live = [have[i] for i in saved if i in have]
            if live:
                return live
    by: dict[str, list[dict[str, Any]]] = {}
    for one in kept():
        by.setdefault(one["group"], []).append(one)
    out = []
    for group in sorted(by):
        out += random.sample(by[group], min(per, len(by[group])))
    note.write_text(json.dumps([one["id"] for one in out]), encoding="utf-8")
    return out


def kept() -> list[dict[str, Any]]:
    """池子裡真的可以用的那幾支：留下來、而且檔案在。"""
    return [one for one in library()["clips"]
            if one.get("keep") and one.get("there")]
