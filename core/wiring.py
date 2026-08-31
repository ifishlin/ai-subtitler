"""收集回來的每一種資料，最後被誰用掉。

## 為什麼要有這一支

報導這件事是這樣發現的：`sheet()` 有把二十三篇撈出來，`as_text()` 從來沒
用過那個欄位 —— 所以它們對成品的貢獻是零，而畫面上一路綠燈。它們還在擋你
（篇數不足不能寫、平衡檢查算它們的立場），但幫不了你。

跟事實那個洞是同一個形狀，只是方向相反：

```
事實    欄位沒有人寫    下游想讀，讀到空的
報導    欄位有人寫      下游根本沒讀
```

兩個都不會有症狀。文案照樣寫得出來，門照樣過，片子照樣壓得出來。

## 所以這一頁不手寫

「這種資料進不進得了 prompt」如果是我打上去的字，它就會過期 —— 這個專案
已經為「兩份會漂的東西」修過四次錯。所以做法是：**真的組一次 prompt，
再拿每一種素材的實際內容回去比對有沒有出現在裡面。**

那表示下一次有人加了一節、或砍掉一節，這一頁自己就會變。報導那個洞如果
早有這一頁，第一天就會看到它寫著「沒有進去」。

`used_by` 那一欄是宣告的（程式看不出「誰在概念上依賴這個欄位」），但每一個
名字都會真的去 import 一次，改名了會顯示 ⚠ 而不是安靜地騙人。
"""
from __future__ import annotations

import importlib
from typing import Any

# 每一種收集回來的資料。
#
# `probe` 從素材堆裡取一段**它自己的實際內容**，然後去組好的 prompt 裡找。
# 找得到就是真的送進去了。取樣而不是全找，因為只要有一段在裡面，那一節就
# 存在；而只要一段都不在，那一節就是不存在。
#
# 拆到欄位而不是只到「影片」，因為同一批東西的不同欄位命運不一樣：影片的
# 字幕進得了 prompt，影片的標題進不了。混在一起講會蓋掉真正的答案。
COLLECTED: list[dict[str, Any]] = [
    {
        "key": "video_file",
        "label": "影片檔案",
        "field": "sources.videos[].file",
        "writer": "topic.bring_in()　（yt-dlp）",
        "why": "剪成會動的段落，以及截新聞畫格",
        "used_by": ["core.captions:passages", "core.topic:cut_frames",
                    "core.build:contact", "core.build:build"],
        "gates": ["ready() 的「還缺 N 支影片」", "still_enough（成片要有會動的）"],
    },
    {
        "key": "video_captions",
        "label": "影片字幕",
        "field": "磁碟上的 .vtt",
        "writer": "topic.bring_in()　（第二次呼叫，跟影片分開）",
        "why": "這條路上唯一拿得到完整句子的地方，而且附帶「誰說的、第幾秒」",
        "used_by": ["core.facts:gather", "core.captions:passages",
                    "core.topic:frame_moments"],
        "gates": ["ready() 的事實那一項（間接）"],
    },
    {
        "key": "video_title",
        "label": "影片標題",
        "field": "sources.videos[].title",
        "writer": "topic.hunt()",
        "why": "給你在畫面上認出這是哪一支；裁決的時候看的也是它",
        "used_by": ["core.writer:sift"],
        "gates": [],
    },
    {
        "key": "reports",
        "label": "報導",
        "field": "sources.reports[]",
        "writer": "topic.gather()　（Google News RSS）",
        "why": "讀正文整理成事實。立場分布比影片好得多 ——"
               "「他們彼此矛盾、或都沒提的地方」只有在這裡才有材料",
        "used_by": ["core.article:text_of", "core.facts:ask_report",
                    "core.topic:balance", "core.topic:counts"],
        "gates": ["ready() 的「還缺 N 篇報導」", "ready() 的平衡檢查"],
    },
    {
        "key": "images",
        "label": "照片",
        "field": "sources.images[]",
        "writer": "topic.replace_images()　（Pexels／維基／畫格）",
        "why": "文案每一句都要有畫面，用編號指定",
        "used_by": ["core.brief:sheet", "core.brief:pick", "core.build:build"],
        "gates": ["ready() 的圖片配比", "unpicked", "unchecked", "uncredited"],
    },
    {
        "key": "voices",
        "label": "鄉民留言",
        "field": "voices[]",
        "writer": "topic.add_voices()",
        "why": "受眾自己的講法，語氣和落地的接觸點從這裡來",
        "used_by": ["core.brief:sheet"],
        "gates": [],
    },
    {
        "key": "facts",
        "label": "整理出的事實",
        "field": "facts[]",
        "writer": "facts.gather()　（讀字幕）",
        "why": "文案每一句陳述事實的都要指得回某一篇",
        "used_by": ["core.brief:sheet", "core.topic:unsourced_facts"],
        "gates": ["ready() 的事實那一項", "unsourced"],
    },
    {
        "key": "lean",
        "label": "立場",
        "field": "sources.*[].lean",
        "writer": "topic.asked_of()　（媒體名單自帶）",
        "why": "確認有沒有哪一邊完全沒被聽到",
        "used_by": ["core.topic:balance"],
        "gates": ["ready() 的「沒有某一邊的說法」"],
    },
    {
        "key": "note",
        "label": "挑這題的理由",
        "field": "note",
        "writer": "你（新增題目的時候）",
        "why": "告訴模型這一題為什麼值得做",
        "used_by": ["core.brief:as_text"],
        "gates": [],
    },
    {
        "key": "audience",
        "label": "說給誰聽",
        "field": "audience",
        "writer": "你，或 topic.suggest_audience() 提議",
        "why": "結論要落在這群人身上；收圖的搜尋詞也照它產生",
        "used_by": ["core.brief:as_text", "core.brief:to_collect"],
        "gates": [],
    },
]


def _sample(key: str, pile: dict[str, Any]) -> str:
    """從素材堆裡取一段這種資料自己的實際內容，用來去 prompt 裡找。

    取實際內容而不是取欄位名，因為欄位名不會出現在 prompt 裡 —— 那樣每一項
    都會回報「沒進去」，而一個永遠說同一個答案的檢查等於沒有檢查。
    """
    from core import topic as topic_module
    if key == "video_file":
        return "[C1]"
    if key == "images":
        return "[P1]"
    if key == "video_captions":
        # 段落裡那句話就是字幕本身。取一段，比對它前面幾個字。
        from core import brief as brief_module
        found = brief_module.sheet(pile["name"])["passages"]
        return (found[0]["said"] or "")[:24] if found else ""
    if key == "video_title":
        videos = topic_module.settled(pile, "videos")
        return (videos[0].get("title") or "")[:24] if videos else ""
    if key == "reports":
        # 報導的貢獻是**經過事實**進到 prompt 的，不是標題直接進去。所以測的
        # 是「有沒有任何一條事實的出處指向一篇報導」—— 那個角括號就是報導
        # 出處的記號（「CNBC〈標題〉」），字幕的出處長「CNN 第 104 秒」。
        return "〈" if any("〈" in one["from"]
                           for one in topic_module.facts_of(pile)) else ""
    if key == "voices":
        for group in pile.get("voices") or []:
            held = group.get("comments")
            said = held[0] if isinstance(held, list) and held else group
            words = str(said.get("text") or "") if isinstance(said, dict) else ""
            if words:
                return words[:20]
        return ""
    if key == "facts":
        found = topic_module.facts_of(pile)
        return (found[0]["say"] or "")[:20] if found else ""
    if key == "lean":
        return ""                      # 不是文字，不會出現在 prompt 裡
    if key == "note":
        return (pile.get("note") or "")[:24]
    if key == "audience":
        return (topic_module.audience(pile) or "")[:12]
    return ""


def _how_many(key: str, pile: dict[str, Any]) -> str:
    """這個題目現在有幾筆這種東西，寫成人看得懂的樣子。

    「5 支」比「sources.videos[].file」好懂，而且看得出這一題到底收到多少。
    """
    from core import facts as facts_module
    from core import topic as topic_module
    videos = topic_module.settled(pile, "videos")
    if key in ("video_file", "video_title"):
        return f"{len([one for one in videos if one.get('file')])} 支"
    if key == "video_captions":
        return f"{len([one for one in videos if facts_module.captions_of(one)])} 支有字幕"
    if key == "reports":
        return f"{len(topic_module.settled(pile, 'reports'))} 篇"
    if key == "images":
        return f"{len(pile.get('sources', {}).get('images') or [])} 張"
    if key == "voices":
        return f"{topic_module.voice_count(pile)} 則"
    if key == "facts":
        return f"{len(topic_module.facts_of(pile))} 條"
    if key == "lean":
        named = {"left": "左", "neutral": "中立", "right": "右", "other": "其他"}
        sides = topic_module.balance(pile)["sides"]
        return "　".join(f"{named.get(k, k)} {v}" for k, v in sides.items())
    if key == "note":
        return "有" if pile.get("note") else "沒填"
    if key == "audience":
        return topic_module.audience(pile) or "沒填"
    return ""


def _resolves(dotted: str) -> bool:
    """`core.topic:balance` 這個名字現在還在嗎。

    宣告的那一欄唯一的風險是改名之後沒人更新它。真的 import 一次，改名就
    會在畫面上顯示 ⚠，而不是繼續指著一個不存在的函式。
    """
    where, _, what = dotted.partition(":")
    try:
        return hasattr(importlib.import_module(where), what)
    except Exception:                                             # noqa: BLE001
        return False


def traced(name: str, house: str = "argue") -> dict[str, Any]:
    """組一次真正的 prompt，回報每一種資料到底有沒有進去。"""
    from core import brief as brief_module
    from core import topic as topic_module

    pile = topic_module.load(name)
    pile["name"] = name
    material = brief_module.as_text(name)

    rows = []
    for spec in COLLECTED:
        held = _sample(spec["key"], pile)
        if not held:
            # 沒有樣本，兩種原因，畫面上要分得開：這個題目沒有這種資料，
            # 或這種資料本來就不是文字。靜靜回報一個「沒進去」是最貴的。
            reaches = None
        else:
            reaches = held in material
        rows.append({**spec,
                     "sample": held,
                     "reaches": reaches,
                     "count": _how_many(spec["key"], pile),
                     "used_by": [{"what": one, "exists": _resolves(one)}
                                 for one in spec["used_by"]]})
    return {"topic": name, "house": house, "rows": rows,
            "material_chars": len(material)}


def parts(name: str, house: str = "argue") -> dict[str, Any]:
    """送進模型的那份文字，拆成一節一節，附上實際大小。

    給的是真的那一份，不是它的描述。描述會過期，`as_text()` 的輸出不會。
    """
    from core import brief as brief_module

    whole = brief_module.prompt(name, house)
    # brief.prompt() 用 `\n\n---\n\n` 分段，而前面有兩份 md（script 和
    # visual），所以切的是**最後一個**分隔線 —— 用第一個的話 visual.md 的
    # 十節會被當成素材，而那一頁就會說「卡片要指定怎麼畫」是一種素材。
    how, split, what = whole.rpartition("\n\n---\n\n")
    if not split:                                    # 接法改了就不要猜
        how, what = "", whole

    sections = []
    current = {"title": "（開頭）", "lines": []}
    for line in what.splitlines():
        if line.startswith("## "):
            sections.append(current)
            current = {"title": line[3:].strip(), "lines": []}
        else:
            current["lines"].append(line)
    sections.append(current)

    out = []
    for one in sections:
        body = "\n".join(one["lines"]).strip("\n")
        items = len([x for x in one["lines"] if x.startswith("- ")
                     or x.startswith("[")])
        out.append({"title": one["title"], "text": body,
                    "chars": len(body), "items": items})
    return {"topic": name, "house": house,
            "how": {"title": "怎麼寫（公版的 prompt）", "text": how,
                    "chars": len(how)},
            "what": out,
            "chars": len(whole),
            # 中文大致一個字一個 token，所以字元數就是夠用的估計。給個數字
            # 比不給好：45 張照片 180 行佔掉 prompt 一大半，那件事看不到。
            "tokens_about": len(whole)}
