"""看一頁渲染出來的樣子，回報畫面上真的錯了的地方。

被 checkview.sh 叫，一次一頁。獨立成檔而不是內嵌 heredoc，因為內嵌那次跟
管線搶 stdin，讀到空字串，於是每一頁都通過 —— 包含故意種了錯的那一次。
"""
import re
import sys

page, path = sys.argv[1], sys.argv[2]
dom = open(path, encoding="utf-8", errors="replace").read()
faults = []

# 一、頁面自己報的錯。reportFault() 把例外寫進面板，所以它出現就是有例外。
_shown = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", dom)
if "頁面出錯了" in _shown or "ReferenceError" in _shown or "TypeError" in _shown:
    found = re.search(r"(頁面出錯了[\s\S]{0,160}|(?:Reference|Type)Error[^<]{0,120})", _shown)
    faults.append("頁面自己報錯：" + re.sub(r"\s+", " ", found.group(1))[:150])

# 二、右半邊還停在佔位。資料抓完就該被換掉；沒換掉表示 boot 沒走完。
stuck = re.search(r'<div id="paper">\s*<div class="empty">(左邊選[^<]*)', dom)
if stuck:
    faults.append(f"右半邊停在佔位：{stuck.group(1)[:40]}")

# 三、共用的東西有沒有真的到位。ask.js 的標記曾經整整沒注入過，而 showAsk
#     照樣定義得好好的 —— 唯一看得出來的方法是找那個元素在不在。
if "/static/ask.js" in dom and 'id="ask"' not in dom:
    faults.append("ask.js 載了但 #ask 沒注入 —— 確認框會失敗，而且是靜靜地")
if "/static/nav.js" in dom and 'class="topnav"' not in dom:
    faults.append("nav.js 載了但導覽列沒出現")

# 只看畫面上的東西 —— <script> 和 <style> 裡面本來就有樣板字面值和關鍵字，
# 那是原始碼不是畫面。第一版沒扣掉，於是每一頁都報一百多處「樣板沒求值」。
shown = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", dom)

# 四、樣板漏出來。${...} 印在畫面上表示某個字串沒被求值就丟進 innerHTML。
leaked = re.findall(r"\$\{[a-zA-Z][^}]{0,40}\}", shown)
if leaked:
    faults.append(f"樣板沒求值就印出來：{leaked[0]}（共 {len(leaked)} 處）")

# 五、undefined / NaN 印在畫面上。那 16 條事實全是 undefined 就是這樣。
for wrong in ("undefined", "NaN"):
    if re.search(rf">\s*{wrong}\s*<", shown):
        faults.append(f"畫面上印出 {wrong}")

for one in faults:
    print(f"  ❌ {page}　{one}")
sys.exit(1 if faults else 0)
