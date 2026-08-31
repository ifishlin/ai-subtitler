"""把報導的網址變成正文。

## 為什麼需要這一支

報導從 Google News RSS 回來只有標題和網址。`core/wiring.py` 那一頁算出來的
結果是：收了二十三篇，**一個字都沒有進到寫文案的 prompt 裡** —— 它們還在
擋你（篇數不足不能寫、平衡檢查算它們的立場），但幫不了你。

事實原本只從五支影片的字幕來，所以同一件事會重複四次（每家講一次），而
`script.md` 開頭要的「找出他們彼此矛盾、或都沒提的地方」根本沒有材料。
二十三篇報導的立場分布比影片好得多，正文才是這件事的來源。

## 抓回來的東西存到磁碟

不是抓來用掉就算了。兩個理由：重跑不用重抓（抓一次要幾分鐘，而且對別人的
站也不禮貌）；更重要的是**抓到什麼你打得開來看**。這條流程上每一個「我以為
它拿到了正確的東西」都出過事 —— 憑搜尋詞挑圖、讀錯自己截的圖、憑記憶寫欄位
名。存成檔案是最便宜的一種「擺在眼前」。

## 抓不到是常態，不是例外

付費牆、擋機器人、整頁都是 JavaScript。Bloomberg 幾乎確定抓不到。所以這一支
**回報失敗的方式跟回報成功一樣重要**：說出是哪一家、為什麼，而不是安靜地少
幾篇。「找到 0 筆」和「沒有去找」在畫面上一模一樣，而後者才是錯。
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

from core import rules as rules_module

# 一篇最多讀幾個字。新聞正文通常兩三千字，長的評論上萬 —— 超過模型讀得完的
# 量之後它會開始跳著讀，而跳著讀的結果看起來跟讀完了一樣。
MOST_CHARS = rules_module.at("article.most_chars", 8000)
# 短到不成篇的，多半抓到的是同意條款或「請開啟 JavaScript」。
LEAST_CHARS = rules_module.at("article.least_chars", 400)
# 每篇之間停一下。字幕那次為了一口氣要四種語言吃到 429，把影片一起帶走了。
PAUSE = rules_module.at("article.pause_seconds", 1.0)

WHERE = ROOT / "assets" / "articles"


def _safe(words: str) -> str:
    """檔名用的一段字。"""
    return re.sub(r"[^\w一-鿿-]+", "-", words).strip("-")[:40] or "x"


def cached(topic: str, report: dict[str, Any]) -> Path:
    """這一篇的正文檔案該放哪裡。

    檔名帶媒體名而不只是雜湊，因為你會想在資料夾裡認出哪一篇是哪一家。
    雜湊取自網址，所以同一篇不會存成兩份。
    """
    url = str(report.get("url") or "")
    stamp = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return WHERE / _safe(topic) / f"{_safe(report.get('outlet', ''))}-{stamp}.txt"


def fetch(url: str) -> tuple[str, str]:
    """抓一篇正文回來。回傳（正文, 抓不到的原因）。

    兩個回傳值只會有一個有東西。用回傳值而不是丟例外，因為抓不到是常態 ——
    二十三篇裡有八篇抓不到是正常的一天，而那不該讓整輪停下來。
    """
    try:
        import trafilatura
    except ImportError:
        return "", "沒裝 trafilatura"
    try:
        page = trafilatura.fetch_url(url)
    except Exception as error:                                    # noqa: BLE001
        return "", f"連不上（{type(error).__name__}）"
    if not page:
        # trafilatura 對 403、404、付費牆的轉址都回 None，分不出來。與其猜，
        # 就說實話：拿不到頁面。
        return "", "拿不到頁面（擋機器人或付費牆）"
    try:
        words = trafilatura.extract(page, include_comments=False,
                                    include_tables=False) or ""
    except Exception as error:                                    # noqa: BLE001
        return "", f"解不開（{type(error).__name__}）"
    words = words.strip()
    if len(words) < LEAST_CHARS:
        return "", f"正文只有 {len(words)} 字，不像文章"
    return words[:MOST_CHARS], ""


def text_of(topic: str, report: dict[str, Any],
            refetch: bool = False) -> tuple[str, str, Path | None]:
    """這一篇的正文：磁碟上有就用磁碟上的，沒有才去抓。

    回傳（正文, 抓不到的原因, 檔案）。抓到就順手存起來 —— 存檔在回傳之前，
    所以就算後面整輪失敗，抓過的東西也還在。破壞性的步驟放最後，而寫檔案
    不是破壞性的。
    """
    where = cached(topic, report)
    if where.is_file() and not refetch:
        words = where.read_text(encoding="utf-8")
        if len(words) >= LEAST_CHARS:
            return words[:MOST_CHARS], "", where

    url = str(report.get("url") or "")
    if not url:
        return "", "沒有網址", None
    words, why = fetch(url)
    time.sleep(PAUSE)
    if not words:
        return "", why, None
    where.parent.mkdir(parents=True, exist_ok=True)
    # 檔頭寫上是哪一篇。一個資料夾裡三十個 txt，沒有這兩行就認不出來。
    where.write_text(f"# {report.get('outlet', '')}　{report.get('title', '')}\n"
                     f"# {url}\n\n{words}\n", encoding="utf-8")
    return words, "", where
