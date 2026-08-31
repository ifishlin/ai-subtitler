#!/bin/bash
# 真的把每一頁打開來看。
#
# checkjs.sh 讀原始碼，這一支讀渲染結果 —— 兩者抓的錯完全不同類。
# 語法檢查不會告訴你「確認框從來沒出現過」、「右半邊停在『左邊選一個』而伺
# 服器回 200」、「字被擠出畫面」。那三個都真的發生過，而且都是靠人眼睛看到
# 的，那表示沒有人看的時候它們就活著。
#
# 需要 Chrome 和一個跑著的伺服器。沒有就跳過，不擋 commit —— 一個在別人機器
# 上必定失敗的檢查會被關掉。
cd "$(dirname "$0")/.." || exit 1

PORT="${PORT:-8000}"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
HOST="http://127.0.0.1:$PORT"

if [ ! -x "$CHROME" ]; then
  echo "  跳過畫面檢查：找不到 Chrome"; exit 0
fi
if ! curl -s -o /dev/null --max-time 3 "$HOST/"; then
  echo "  跳過畫面檢查：$HOST 沒有在跑"; exit 0
fi

bad=0
seen=$(mktemp)
# 要看哪幾頁。給參數就只看那幾頁 —— 種一個錯回去驗證的時候，開八次瀏覽器
# 要兩分鐘，而只驗一頁要十秒。
PAGES="${*:-/ /topics /scripts /produce /desk /assemble /gates /docs}"
for page in $PAGES; do
  "$CHROME" --headless --disable-gpu --virtual-time-budget=7000 \
    --dump-dom "$HOST$page" > "$seen" 2>/dev/null
  if [ ! -s "$seen" ]; then
    echo "  ❌ $page 沒有回應"; bad=1; continue
  fi
  # DOM 用檔案傳，不用管線。本來是 `printf '%s' "$out" | python3 - <<'PY'`
  # —— 管線和 heredoc 搶同一個 stdin，heredoc 贏了，於是那支腳本每次讀到的
  # 都是空字串，對每一頁都印 ✅，包含我故意種了錯的那一次。
  # 一個永遠說通過的檢查，比沒有檢查更糟：它讓人以為看過了。
  python3 studio/_viewcheck.py "$page" "$seen"
  if [ $? -eq 0 ]; then echo "  ✅ $page"; else bad=1; fi
done
rm -f "$seen"

exit $bad
