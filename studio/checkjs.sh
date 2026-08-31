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
  native=$(grep -o '\balert(\|\bconfirm(\|\bprompt(' /tmp/_page.mjs | wc -l | tr -d ' ')
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

exit $bad
