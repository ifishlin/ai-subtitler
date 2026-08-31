"""把每一種卡畫出來，掃邊界，回報超出版面的。

## 為什麼是掃圖，不是算寬度

算寬度要知道每一支畫法的版面 —— 而版面就在那十二支函式裡，各寫各的。
一份「檢查用的版面知識」和「畫圖用的版面知識」是兩份，而這個專案為
「兩份會漂的東西」修過五次。

畫出來的圖沒有這個問題：它就是最後那一張。邊界上有墨，就是超出去了。

## 三組內容

正常、很長、極長。前兩組是真的文案裡出現過的長度；第三組是「有一天會發生」
的長度 —— 而它會發生在半夜自動跑的那一次，不是在有人看著的時候。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import cards as cards_module

# 三種長度。每一組都是一個「這個欄位要多長」的字典。
SHORT, LONG, HUGE = "正常", "很長", "極長"

WORDS = {
    SHORT: "改名了",
    LONG: "地圖本來是大家共用的",
    HUGE: "改掉的不是湖的名字而是你手機裡那張地圖上的每一個字",
}
# 一行字的長度。number 的 value 是大字，比別的更容易爆版。
VALUES = {SHORT: "18", LONG: "4:30", HUGE: "一九四五年八月十五日"}


def cases(kind: str) -> list[tuple[str, dict]]:
    """這一種卡的三組測試內容。"""
    out = []
    for size in (SHORT, LONG, HUGE):
        word, value = WORDS[size], VALUES[size]
        spec: dict = {"kind": kind, "tone": "warm", "title": word,
                      "note": "PBS NewsHour", "under": word}
        if kind == "bars":
            spec["rows"] = [[word, 50, "", value], [word, 12, "", value],
                            [word, 3, "", value]]
        elif kind == "split":
            spec["branches"] = [word, word]
        elif kind == "stack":
            spec["items"] = [word, word, word, word]
        elif kind == "chain":
            spec["points"] = [word, word, word]
        elif kind == "queue":
            spec["count"] = 7 if size != HUGE else 40
        elif kind == "clock":
            spec["part"], spec["value"] = 0.6, value
        elif kind in ("ring", "number"):
            spec["value"] = value
        elif kind == "swap":
            spec["was"], spec["now"] = word, word
        elif kind == "outro":
            spec["points"] = [word] * (3 if size != HUGE else 4)
        out.append((size, spec))
    return out


def over(image, margin: int) -> dict[str, int]:
    """哪幾個邊被碰到，各差多少像素。

    背景是漸層，所以「什麼是墨」用「跟同一列最左邊那格差多少」判斷，不是
    比對一個固定的底色。
    """
    width, height = image.size
    pixels = image.convert("RGB").load()

    def inked(x: int, y: int) -> bool:
        ground = pixels[1, y]
        here = pixels[x, y]
        return sum(abs(a - b) for a, b in zip(here, ground)) > 45

    worst = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    for y in range(2, height - 2, 2):
        for x in range(1, margin):
            if inked(x, y):
                worst["left"] = max(worst["left"], margin - x)
                break
        for x in range(width - 2, width - margin, -1):
            if inked(x, y):
                worst["right"] = max(worst["right"], x - (width - margin))
                break
    # 上下用同一個標準：畫面外緣不該有東西。字幕區的碰撞是另一回事，
    # 這一支只管「有沒有畫出畫布」。
    for x in range(2, width - 2, 2):
        for y in range(1, 30):
            if inked(x, y):
                worst["top"] = max(worst["top"], 30 - y)
                break
        for y in range(height - 2, height - 30, -1):
            if inked(x, y):
                worst["bottom"] = max(worst["bottom"], y - (height - 30))
                break
    return {edge: n for edge, n in worst.items() if n > 0}


def main() -> int:
    want = sys.argv[1:] or sorted(cards_module.KINDS)
    margin = cards_module.MARGIN
    faults = 0
    print(f"畫布 {cards_module.W}×{cards_module.H}　留白 {margin}px")
    for kind in want:
        if kind not in cards_module.KINDS:
            print(f"  ❌ 沒有這種卡：{kind}")
            faults += 1
            continue
        bad = []
        for size, spec in cases(kind):
            # t=1.0：全部畫完的那一格。動畫中間比較窄，不會更寬。
            image = cards_module.draw(spec, 1.0)
            found = over(image, margin)
            if found:
                bad.append((size, found))
        if bad:
            faults += len(bad)
            for size, found in bad:
                where = "、".join(f"{edge} 超出 {n}px"
                                 for edge, n in sorted(found.items()))
                print(f"  ❌ {kind:7} {size}　{where}")
        else:
            print(f"  ✅ {kind}")
    if not faults:
        print("  三種長度都沒有畫出版面")
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main())
