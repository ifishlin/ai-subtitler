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
node --check studio/static/ask.js && echo "  ✅ ask.js" || bad=1
exit $bad
