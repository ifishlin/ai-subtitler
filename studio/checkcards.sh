#!/bin/bash
# 每一種卡，畫出來看有沒有超出版面。
#
# 「字被切掉」是這個專案最貴的一類錯：沒有例外、沒有 stack trace、測試不會
# 失敗，程式跑得完完全全成功。它只在有人看的時候才存在 —— 而那是壓完片、
# 四分鐘之後的事。
#
# 所以這一支不看程式碼，也不用眼睛：**真的把卡畫出來，掃四個邊，看有沒有
# 墨碰到邊界。** cards.py 自己的註解寫著「第三次把字畫出畫面」，而那之後
# 又發生了第四次（chain 的點標籤）和第五次（同一張，我以為修好了）。
#
# 每一種卡都用三組內容測：正常、很長、極長。極限狀況才是會出事的那些。
cd "$(dirname "$0")/.." || exit 1
.venv/bin/python studio/_cardcheck.py "$@" || bad=1

# 折行有沒有把一個拉丁字切成兩半。
#
# 邊界掃描看不到這一種：`Netflix` 折成 `Ne` 和 `tflix` 的時候，沒有一個字素
# 碰到邊界，卡片畫得出來，寬度也量過了 —— 每一道門都過。它只在有人讀那兩行
# 的時候才存在，而成片上它已經在那裡了。
echo "檢查折行有沒有切開拉丁字…"
.venv/bin/python - <<'PY' || bad=1
import sys, re
sys.path.insert(0, ".")
from core import cards

# 真的在成片上出現過的那些，加上幾個更擠的。room 從很鬆到很緊。
WORDS = ["支持 Netflix", "每股 27.75 美元", "CBS News 加上 CNN",
         "Warner Bros Discovery", "Lake Ontario 變成 Lake America",
         "來自 TikTok", "Google 只對美國用戶改", "2.7GW 的機房"]
faults = 0
for text in WORDS:
    for room in range(180, 620, 20):
        _step, rows = cards.wrap_at(text, 64, room)
        joined = "".join(rows)
        # 折行只准在原本就有空白的地方多斷。把兩邊的拉丁字拿出來比：
        # 原文有幾個完整的字，折完就該還有幾個。
        was = re.findall(r"[A-Za-z0-9][A-Za-z0-9.'-]*", text.replace(" ", "\n"))
        now = re.findall(r"[A-Za-z0-9][A-Za-z0-9.'-]*",
                         "\n".join(one.replace(" ", "\n") for one in rows))
        if was == now or len(joined) < len(text.replace(" ", "")):
            continue                       # 後者是「這個字再怎樣都放不下」
        print(f"  ❌ 「{text}」在 {room}px 被折成 {rows}"
              f"　—— 拉丁字被切開了：{was} → {now}")
        faults += 1
        break
if not faults:
    print("  ✅ 沒有")
sys.exit(1 if faults else 0)
PY

exit ${bad:-0}
