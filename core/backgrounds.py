"""卡片背景照片的共用池子 —— 不屬於任何一個題目。

`card.bg` 挑的是這個題目自己收的素材，跟這句話理應有點關係；`bg_search`
不是——它是卡片找不到合用照片時的備援，找的是「氣氛對了就好」的裝飾照片，
跟題目內容無關。既然無關，就不必每個題目各自抓一份：同一個關鍵字（「鈔票
特寫」、「海邊風景」）在十個題目底下用十次，應該共用同一批圖，而不是
重複下載十次、重複消耗十次 Pexels 額度。

一個關鍵字最多存 `MAX_PER_KEYWORD` 張。存滿之後不再搜尋，直接從裡面隨機
挑一張——這樣同一個關鍵字一輩子只會打那麼多次 API，用得越久共用機會
越高。殺掉一張要記進 `rejected`，不然下次補的時候 Pexels 排序穩定，很
可能又搜回同一張剛被殺掉的圖。
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from core import stock as stock_module

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "assets" / "backgrounds.json"
DIR = ROOT / "assets" / "backgrounds"
MAX_PER_KEYWORD = 5


def _load() -> dict[str, Any]:
    if not STORE.is_file():
        return {"images": {}, "keywords": {}}
    return json.loads(STORE.read_text(encoding="utf-8"))


def _save(pool: dict[str, Any]) -> None:
    STORE.write_text(json.dumps(pool, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def top_keywords(limit: int = 50) -> list[str]:
    """送進 prompt 給 LLM 看的關鍵字清單，只送最常被重用的前幾個。

    這份清單會隨系統用得越久一直長大——跟題目自己的素材清單不一樣，那些
    收完材料就固定大小了，這份是跨題目、跨時間累積的，沒有天花板。全部
    送進去的話 prompt 會沒有上限地變大，所以只送前 `limit` 個，用「這個
    關鍵字底下存了幾張圖」當熱門程度的替代指標——存得越滿代表被重用的
    次數越多。
    """
    pool = _load()
    ranked = sorted(pool.get("keywords", {}).items(),
                     key=lambda pair: len(pair[1].get("kept") or []),
                     reverse=True)
    return [key for key, _ in ranked[:limit]]


def resolve(keyword: str) -> dict[str, Any]:
    """這個關鍵字現在指到哪一張圖。

    已經有存的，直接從最多五張裡隨機挑一張，不打 Pexels。沒有的話現搜
    現抓，一次補到五張（或 Pexels 給得出的最多張），再從裡面挑一張。
    """
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("bg_search 的關鍵字不能是空的")
    pool = _load()
    bucket = pool.setdefault("keywords", {}).setdefault(
        keyword, {"kept": [], "rejected": []})
    if len(bucket["kept"]) < MAX_PER_KEYWORD:
        # 沒滿五張就再搜一次，不管現在是 0 張還是被殺過剩 4 張——殺掉的
        # 那張已經記在 `rejected` 裡，搜到也會被跳過，不會補回同一張。
        _fill(pool, keyword, bucket)
        _save(pool)
    if not bucket["kept"]:
        raise ValueError(f"關鍵字「{keyword}」在圖庫裡找不到合用的照片")
    chosen = random.choice(bucket["kept"])
    return pool["images"][chosen]


def _fill(pool: dict[str, Any], keyword: str, bucket: dict[str, Any]) -> None:
    """幫這個關鍵字補到 `MAX_PER_KEYWORD` 張，跳過已經殺掉、或系統裡已經
    有的太像的圖（用 `stock.alike()` 判斷，不是只比對檔名）。"""
    exclude = set(bucket["kept"]) | set(bucket.get("rejected") or [])
    known_looks = [pool["images"][key]["look"] for key in pool.get("images", {})
                   if key not in exclude]
    try:
        candidates = stock_module.search_photos(
            keyword, count=MAX_PER_KEYWORD * 4)
    except Exception as error:                                    # noqa: BLE001
        raise ValueError(f"搜尋「{keyword}」失敗：{error}") from error
    for picture in candidates:
        if len(bucket["kept"]) >= MAX_PER_KEYWORD:
            break
        image_id = f"pexels-{picture.id}"
        if image_id in exclude:
            continue                      # 這個關鍵字已經有它，或曾經殺掉它
        if image_id in pool.get("images", {}):
            # 別的關鍵字已經抓過同一張圖，檔案已經在，直接共用不用重抓。
            bucket["kept"].append(image_id)
            continue
        DIR.mkdir(parents=True, exist_ok=True)
        target = DIR / f"{image_id}.jpg"
        try:
            stock_module.fetch(picture.url, target)
        except Exception:                                         # noqa: BLE001
            continue
        look = stock_module.looks_like(target)
        if any(stock_module.alike(look, other) for other in known_looks):
            target.unlink(missing_ok=True)
            continue
        pool.setdefault("images", {})[image_id] = {
            "file": str(target.relative_to(ROOT)), "provider": "pexels",
            "id": picture.id, "kind": "stock", "term": keyword,
            "outlet": "Pexels", "author": picture.author, "credit": "",
            "page": picture.page, "caption": picture.about,
            "look": look, "seen": True}
        bucket["kept"].append(image_id)
        known_looks.append(look)


def reject(keyword: str, image_id: str) -> None:
    """把一張圖從這個關鍵字底下殺掉，永久記住不要再補到它。"""
    pool = _load()
    bucket = pool.get("keywords", {}).get(keyword)
    if not bucket:
        return
    if image_id in (bucket.get("kept") or []):
        bucket["kept"].remove(image_id)
    if image_id not in (bucket.get("rejected") or []):
        bucket.setdefault("rejected", []).append(image_id)
    _save(pool)


def images_by_file() -> dict[str, dict[str, Any]]:
    """這個池子裡的每張圖，用檔案路徑當 key——`script.gathered()` 要合併
    這個池子跟題目自己的素材時，兩邊的鍵要對得上（`card.bg` 存的就是
    檔案路徑，不是 `pexels-<id>` 那種內部代稱）。"""
    return {item["file"]: item for item in _load().get("images", {}).values()
            if item.get("file")}


def all_keywords() -> dict[str, Any]:
    """給檢視畫面用：每個關鍵字底下有哪些圖（含檔案路徑），不含 `rejected`
    的（那些已經被殺掉，不用再顯示）。

    `key` 才是殺掉一張圖時要傳回來的那個值（`pexels-27978377` 這種池子
    內部的代稱）——不能疊在 `**images[key]` 後面，那個字典自己也有一個
    `id` 欄位（Pexels 自己的數字編號，不帶前綴），先展開的話會被蓋掉。
    """
    pool = _load()
    images = pool.get("images", {})
    return {keyword: [{**images[key], "key": key} for key in bucket.get("kept") or []
                       if key in images]
            for keyword, bucket in pool.get("keywords", {}).items()}
