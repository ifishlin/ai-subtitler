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
python3 - <<'PY' || bad=1
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

# 短鏡頭壓得出來嗎。
#
# `loudnorm` 要大約三秒才算得出東西 —— 給它 1.9 秒，它回 NaN，aac 拒收，
# 整支片壓不出來。這個錯只有在「剛好有一句很短、而且配的是會動的畫面」
# 的時候才出現，而那在情境影片進來之前一次都沒發生過。
#
# 檢查的方式是真的壓一段出來：音訊濾鏡鏈的行為讀程式碼看不出來，
# 只有 ffmpeg 自己知道。
echo "檢查很短的鏡頭壓不壓得出來…"
.venv/bin/python - <<'PY' || bad=1
import subprocess, sys, tempfile, pathlib
sys.path.insert(0, ".")
from core import shorts, build

# 自己造一段輸入，不去找硬碟上的檔案。第一版拿 assets/broll 排序後的第一支
# 來測，而那一支的音訊剛好不會讓 loudnorm 算出 NaN —— 種了錯回去，門說通過。
#
# 會爆的條件是**數位靜音**：loudnorm 對著全 0 的樣本算增益，算出無限大。
# 所以測試輸入要自己合成，保證每次都踩得到，而不是碰運氣。
def make(room):
    src = pathlib.Path(room) / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=640x360:rate=30:duration=6",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=6",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(src), "-y"], check=True)
    return src

faults = 0
with tempfile.TemporaryDirectory() as room:
    here = make(room)
    for secs in (0.6, 1.0, 1.9, 2.9, 3.0):
        out = pathlib.Path(room) / f"{secs}.mp4"
        try:
            shorts.clip_cut(here, 0.0, max(0.4, secs), secs, out)
        except Exception as error:                                # noqa: BLE001
            print(f"  ❌ {secs} 秒的鏡頭壓不出來：{str(error)[:80]}")
            faults += 1
            continue
        if not out.is_file() or out.stat().st_size < 2000:
            print(f"  ❌ {secs} 秒壓出來是空的")
            faults += 1
if not faults:
    print("  ✅ 都壓得出來")
sys.exit(1 if faults else 0)
PY

exit ${bad:-0}
