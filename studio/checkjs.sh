#!/bin/bash
# Node parses the page's script the way the browser will. A stray await, an
# unbalanced brace or a broken template literal is a blank page, and the server
# still returns 200 -- so "the page loads" proves nothing.
f="${1:-studio/static/scripts.html}"
python3 - "$f" <<'PY' > /tmp/_page.mjs
import sys, pathlib
p = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
sys.stdout.write(p[p.index("<script>") + 8: p.rindex("</script>")])
PY
node --check /tmp/_page.mjs
