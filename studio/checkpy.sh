#!/bin/bash
# 同一個模組裡同名的 def，Python 不會警告，後面那個安靜地蓋掉前面那個。
#
# 我在 core/script.py 加了一個 too_long(measured)，而檔案下面早就有一個
# too_long(script)。後定義的贏，所以 build() 把一個 measured 字典傳給了一個
# 要 script 的函式 —— 壓片的長度檢查就這樣停止工作。沒有任何錯誤：錯的那個
# 函式回了一個空清單，而空清單在 Python 裡是 false、在 JavaScript 裡是 true，
# 所以網頁反而把每一支片都標成超長。
#
# 一個 grep 抓不到這個（兩行長得不一樣），但 ast 一秒就抓到。
cd "$(dirname "$0")/.." || exit 1
python3 - <<'PY'
import ast, pathlib, sys, collections
bad = 0
for path in sorted(pathlib.Path(".").glob("core/*.py")) + \
            sorted(pathlib.Path(".").glob("studio/*.py")):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as error:
        print(f"  ❌ {path}　語法錯誤第 {error.lineno} 行：{error.msg}")
        bad = 1
        continue
    seen = collections.defaultdict(list)
    for node in tree.body:                       # 只看最上層，方法可以同名
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seen[node.name].append(node.lineno)
        elif isinstance(node, ast.ClassDef):
            seen[node.name].append(node.lineno)
    twice = {name: lines for name, lines in seen.items() if len(lines) > 1}
    if twice:
        for name, lines in twice.items():
            where = "、".join(f"第 {n} 行" for n in lines)
            print(f"  ❌ {path}　{name}() 定義了 {len(lines)} 次（{where}）"
                  f"　—— 後面那個會蓋掉前面的")
        bad = 1
    else:
        print(f"  ✅ {path}")
sys.exit(bad)
PY
