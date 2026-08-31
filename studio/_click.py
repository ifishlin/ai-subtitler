"""開一頁、按一顆鈕、把按完的畫面倒出來。

`checkview.sh` 讀的是**載入之後**的畫面，抓不到「點下去之後」的事。而這個
專案有一顆按鈕從來沒有作用過，錯的形狀正是那個：`ask.js` 在自己的
DOMContentLoaded 注入之前就去綁 `$("askYes").onclick`，於是每一次確認框
都靜靜地不出現，而載入時的畫面完全正常。

用法：

    python3 studio/_click.py /topics?name=X btnShow rawText

參數是「哪一頁、按哪顆鈕、按完之後印哪個元素」。
"""
from __future__ import annotations

import asyncio
import json
import sys
import urllib.request


def target(port: int) -> str:
    """Chrome 開著的第一個分頁的 WebSocket 位址。"""
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json") as answer:
        for page in json.load(answer):
            if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                return page["webSocketDebuggerUrl"]
    raise SystemExit("Chrome 沒有可用的分頁")


async def run(where: str, button: str, show: str, port: int) -> int:
    import websockets
    async with websockets.connect(target(port), max_size=40_000_000) as ws:
        step = 0

        async def send(method: str, **params):
            nonlocal step
            step += 1
            await ws.send(json.dumps({"id": step, "method": method,
                                      "params": params}))
            while True:
                got = json.loads(await ws.recv())
                if got.get("id") == step:
                    return got.get("result", {})

        async def js(source: str):
            got = await send("Runtime.evaluate", expression=source,
                             awaitPromise=True, returnByValue=True)
            return (got.get("result") or {}).get("value")

        await send("Page.enable")
        await send("Page.navigate", url=f"http://127.0.0.1:8000{where}")
        # 等頁面把自己畫完。這一頁的內容是 fetch 回來之後才 render 的，
        # 所以等的是元素出現，不是 load 事件。
        for _ in range(60):
            await asyncio.sleep(0.25)
            if await js(f"!!document.getElementById({button!r})"):
                break
        else:
            print(f"❌ 等不到 {button}")
            return 1

        await js(f"document.getElementById({button!r}).click()")
        # 按下去之後要打 API，等它回來。
        for _ in range(80):
            await asyncio.sleep(0.25)
            text = await js(
                f"(document.getElementById({show!r})||{{}}).textContent || ''")
            if text and "組裝中" not in text:
                break

        opened = await js("(() => {"
                          " const box = document.getElementById('raw');"
                          " return box ? !box.hidden : null; })()")
        jump = await js("(() => {"
                        " const bar = document.getElementById('rawJump');"
                        " return bar ? bar.innerText.replace(/\\n/g,' / ') : ''"
                        "; })()")
        size = await js("(document.getElementById('rawSize')||{}).textContent")
        faults = await js("window.__faults ? window.__faults.length : 0")

        print(f"覆蓋層打開了：{opened}")
        print(f"大小顯示　　：{size}")
        print(f"跳點　　　　：{jump}")
        print(f"回報的錯誤　：{faults}")
        print("-" * 60)
        print((text or "")[:700])
        return 0 if opened else 1


if __name__ == "__main__":
    where, button, show = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.exit(asyncio.run(run(where, button, show, 9222)))
