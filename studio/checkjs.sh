#!/bin/bash
# Node parses each page's script the way the browser will. A stray await, an
# unbalanced brace or a broken template literal is a blank page, and the server
# still returns 200 -- so "the page loads" proves nothing.
#
# It also refuses the browser's own dialogs. They arrive in the platform's
# chrome saying 127.0.0.1:8000 說, and they cannot show what is about to be
# lost, which is the whole of the question when the prompt is 刪掉？
# Use confirmed() / asked() / told() from static/ask.js.
cd "$(dirname "$0")/.." || exit 1
bad=0
for f in studio/static/*.html; do
  python3 - "$f" > /tmp/_page.mjs <<'PY'
import re, sys, pathlib
# Every inline block, each on its own, joined. It used to take everything
# between the first <script> and the *last* </script>, so adding a single
# <script src="..."></script> at the end swallowed the closing tag of the
# real block and node reported a syntax error in code that was fine.
#
# Blocks with attributes are skipped: a src= block is a file, and files are
# checked as files.
page = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
blocks = re.findall(r"<script>(.*?)</script>", page, re.S)
sys.stdout.write("\n;\n".join(blocks))
PY
  if node --check /tmp/_page.mjs 2>/tmp/_err; then
    echo "  ✅ $(basename "$f")"
  else
    # node prints the offending line, a caret, then the message. All three are
    # worth having: "❌ produce.html" with nothing after it says only that
    # something is wrong somewhere in three thousand lines.
    echo "  ❌ $(basename "$f")"
    sed -n '2,6p' /tmp/_err | sed 's/^/       /'
    bad=1
  fi
  # `\bprompt(` 也會咬到 `brief.prompt()` —— `.` 是非字元，所以 `\b` 在它
  # 後面成立。/material 那一頁在說明文字裡提到 brief.prompt()，就被報成一個
  # 原生對話框。誤判的檢查最後會被關掉，所以規則要準：前面不能是點或字元，
  # 但 `window.alert(` 那種真的要抓。
  native=$( { grep -oE '(^|[^.[:alnum:]_$])(alert|confirm|prompt)\(' /tmp/_page.mjs
              grep -oE '\bwindow\.(alert|confirm|prompt)\(' /tmp/_page.mjs
            } | wc -l | tr -d ' ')
  if [ "$native" != "0" ]; then
    echo "     ⚠ 還有 $native 個瀏覽器原生對話框，改用 confirmed()／asked()／told()"; bad=1
  fi
done
for f in studio/static/*.js; do
  node --check "$f" && echo "  ✅ $(basename "$f")" || bad=1
done

# 頂層去摸一個「頁面裡沒有」的元素。
#
# ask.js 把對話框標記從各頁 HTML 搬進自己注入之後，舊的那份頂層綁定留在原地：
# $("askYes").onclick 在注入之前執行，askYes 是 null，null.onclick 當場拋錯，
# 於是底下真正要注入標記的那一段永遠沒跑到。五個頁面都載了 ask.js、showAsk 和
# confirmed 都定義好了，而 #ask 從來不存在 —— 任何一次確認框都會失敗，而且不
# 會顯示成錯誤，只會顯示成「按了刪除，什麼都沒發生」。
#
# 只有「頁面的靜態標記裡找不到那個 id」才算錯。頂層綁一個本來就寫在 HTML 裡的
# 元素完全正常，那是這幾頁上百處的做法。而 .js 檔沒有自己的標記，所以它在頂層
# 摸任何 id 都是在賭別人已經畫好了。
echo "檢查頂層有沒有摸到不存在的元素…"
python3 - <<'PY' || bad=1
import re, sys, pathlib
faults = 0
for path in sorted(pathlib.Path("studio/static").glob("*.js")) + \
            sorted(pathlib.Path("studio/static").glob("*.html")):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".html":
        code = "\n;\n".join(re.findall(r"<script>(.*?)</script>", text, re.S))
        markup = re.sub(r"<script>.*?</script>", "", text, flags=re.S)
        owns = set(re.findall(r'id="([^"{}]+)"', markup))
    else:
        code, owns = text, set()
    for number, line in enumerate(code.splitlines(), start=1):
        found = re.match(r'^\$\("([a-zA-Z][\w-]*)"\)', line)
        if found and found.group(1) not in owns:
            print(f"  ❌ {path.name}　第 {number} 行：$(\"{found.group(1)}\") "
                  f"在頂層，而 {'這個檔案沒有標記' if not owns else 'HTML 裡沒有這個 id'}")
            faults += 1
if not faults:
    print("  ✅ 沒有")
sys.exit(1 if faults else 0)
PY

# 沒有人用的 CSS 選擇器。
#
# 拆一頁變兩頁之後，兩邊各留下二十幾條對方才用的規則 —— 素材頁帶著 .rights、
# .shape、.turn 的樣式，短影音頁帶著 .doubted、.wanted、.shelf。全部沒有害，
# 全部讓下一個讀的人以為那些東西還在這一頁上。
#
# 只看 `  .x{` 這種兩格縮排的頂層規則，不看巢狀和組合選擇器 —— 寧可漏抓，
# 不要誤報，誤報的門會被關掉。
echo "檢查有沒有沒人用的 CSS…"
python3 - <<'PY' || bad=1
import re, sys, pathlib
# shared.js 注入的東西、leanTag 產生的 class：不在 HTML 裡但活著
BORN_IN_JS = {"#big", "#bigNote", "#bigStage", ".big-bar", ".big-inner",
              ".lean", ".topnav", ".ask-inner", ".ask-buttons", ".ask-lose",
              "#ask", "#askTitle", "#askBody", "#askText", "#askYes", "#askNo"}
faults = 0
for path in sorted(pathlib.Path("studio/static").glob("*.html")):
    text = path.read_text(encoding="utf-8")
    if "<style>" not in text:
        continue
    css = text[text.index("<style>") + 7:text.index("</style>")]
    rest = text.replace(css, "")
    dead = []
    for found in re.finditer(r"^\s{2}([.#][\w-]+)(?=[\s,{:>])", css, re.M):
        sel, key = found.group(1), found.group(1)[1:]
        if sel in BORN_IN_JS:
            continue
        want = (rf'class="[^"]*\b{re.escape(key)}\b' if sel[0] == "."
                else rf'id="{re.escape(key)}"')
        if re.search(want, rest) or f'"{key}"' in rest or f"'{key}'" in rest:
            continue
        dead.append(sel)
    for sel in sorted(set(dead)):
        print(f"  ❌ {path.name}　{sel} 沒有人用")
        faults += 1
if not faults:
    print("  ✅ 沒有")
sys.exit(1 if faults else 0)
PY

# 頂層宣告一個跟 window 內建同名的東西，就把那個內建蓋掉了。
#
# topics.html 有 `async function open(name)`，於是 show 那顆鈕呼叫的
# `window.open(網址)` 進到了那一支，把整個網址當成題目名稱 —— 畫面上寫
# 「打不開『/raw?name=…&house=argue』」。函式宣告在腳本開始跑之前就掛上
# window，所以連「先存一份原生的」都辦不到；只能不要取那個名字。
#
# 而這種錯測不出來的原因特別壞：我驗證的時候把 window.open 換成假的來記
# 參數，換掉的正好就是壞掉的那一個，所以測起來完全正常。
echo "檢查有沒有蓋掉瀏覽器內建的名字…"
python3 - <<'PY' || bad=1
import re, sys, pathlib

# 只列真的會被當成函式名、而且蓋掉會出事的。`name`、`length`、`status`
# 這種當變數名太常見，另外處理才有意義 —— 一條會誤報的規則會被關掉。
TAKEN = ("open", "close", "print", "focus", "blur", "stop", "find",
         "alert", "confirm", "prompt", "scroll", "scrollTo", "scrollBy",
         "postMessage", "getSelection", "matchMedia", "history", "location",
         "navigator", "screen")
faults = 0
for path in sorted(pathlib.Path("studio/static").glob("*.html")) + \
            sorted(pathlib.Path("studio/static").glob("*.js")):
    text = path.read_text(encoding="utf-8")
    for kind in TAKEN:
        # 頂層（行首沒有縮排）的宣告才會掛到 window 上。
        hit = re.search(rf"^(?:async\s+)?function\s+{kind}\s*\(", text, re.M)
        if not hit:
            hit = re.search(rf"^(?:var|let|const)\s+{kind}\s*=", text, re.M)
        if hit:
            line = text[:hit.start()].count("\n") + 1
            print(f"  ❌ {path.name}:{line}　頂層的 {kind} 蓋掉了 window.{kind}"
                  f"　—— 改個名字")
            faults += 1
if not faults:
    print("  ✅ 沒有")
sys.exit(1 if faults else 0)
PY

# 每一頁都要有導覽列和共用配色，不然它會長得像另一個系統。
echo "檢查每一頁有沒有接上共用的東西…"
python3 - <<'PY' || bad=1
import sys, pathlib
# 從別的頁面彈出來的視窗。它不是一個服務，沒有人會直接走到它 —— 這條規則
# 是為了「每一頁都到得了別頁」而存在的，而一扇窗的回去就是關掉它。
# 配色還是要共用：那一條沒有例外。
POPUPS = {"raw.html"}
faults = 0
for path in sorted(pathlib.Path("studio/static").glob("*.html")):
    text = path.read_text(encoding="utf-8")
    for need, why in (("/static/theme.css", "共用配色"),
                      ("/static/nav.js", "導覽列")):
        if need == "/static/nav.js" and path.name in POPUPS:
            continue
        if need not in text:
            print(f"  ❌ {path.name}　沒有載 {need}（{why}）")
            faults += 1
if not faults:
    print("  ✅ 都有")
sys.exit(1 if faults else 0)
PY

exit $bad
