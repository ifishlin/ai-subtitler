"""刪東西的唯一入口：不真的刪，移到 `trash/`。

今晚掃舊素材的時候，我少數了一種指標 —— 一支影片有 `file` 和 `captions`
兩個檔案，程式只收了 `file`，四十四個字幕檔全被掃走。畫面上的樣子是
「段落 0」，跟「這幾支本來就沒有字幕」一模一樣。

那次沒有損失，唯一的原因是**掃的是移到 trash 不是真刪**。而那是我當下記得
要那樣寫，不是制度 —— 下一支掃檔的程式由誰寫、記不記得，沒有人保證。

所以：**要丟東西就叫 `toss()`。** 它做三件事：

  移到 `trash/<日期時間>-<為什麼>/`，保留原本的相對路徑
  寫一行紀錄，說是誰、為什麼、什麼時候丟的
  回報丟了幾個、多大

不適用的是**衍生檔**：壓片中途的鏡頭片段、卡片的示範影片、換了指紋之後
沒人指著的快取。那些每次重跑都會重生，丟進 trash 只會把 trash 塞滿，
所以它們照舊直接 unlink。分界是一句話：**重跑補得回來的，直接刪；
重跑補不回來的，`toss()`。**
"""
from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
TRASH = ROOT / "trash"


def toss(paths: Iterable[Path | str], why: str) -> dict[str, object]:
    """把這些東西移到 trash。回報丟了什麼。

    `why` 會變成資料夾名字的一部分，所以三個月後回頭看得出那一批是什麼。
    寫成必填而不是選填：一個叫 `trash/20260901-101530` 的資料夾，跟一個
    叫 `trash/舊素材-20260901-101530` 的資料夾，救援的時候差很多。
    """
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch for ch in why if ch not in '/\\:*?"<>|').strip() or "刪除"
    room = TRASH / f"{safe}-{stamp}"

    moved, freed = [], 0
    for one in paths:
        here = Path(one)
        if not here.is_absolute():
            here = ROOT / here
        if not here.exists():
            continue
        # 原本的相對路徑保留下來 —— 救回去的時候才知道它本來在哪。
        try:
            keep = here.resolve().relative_to(ROOT)
        except ValueError:
            keep = Path(here.name)
        size = (sum(f.stat().st_size for f in here.rglob("*") if f.is_file())
                if here.is_dir() else here.stat().st_size)
        target = room / keep
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(here), str(target))
        moved.append(str(keep))
        freed += size

    if not moved:
        return {"moved": 0, "bytes": 0, "room": None}

    # 紀錄寫進檔案，不只寫進畫面。上一次那句 ⚠ 出現過，重啟伺服器就沒了。
    (room / "為什麼.json").write_text(json.dumps(
        {"why": why, "when": stamp, "files": moved, "bytes": freed},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"moved": len(moved), "bytes": freed,
            "room": str(room.relative_to(ROOT))}


def rooms() -> list[dict[str, object]]:
    """trash 裡有哪幾批，各是什麼、多大。給人看的，不是給程式用的。"""
    if not TRASH.is_dir():
        return []
    out = []
    for room in sorted(TRASH.iterdir(), reverse=True):
        if not room.is_dir():
            continue
        note = room / "為什麼.json"
        said = {}
        if note.is_file():
            try:
                said = json.loads(note.read_text(encoding="utf-8"))
            except Exception:                                     # noqa: BLE001
                said = {}
        out.append({
            "room": room.name,
            "why": said.get("why", ""),
            "when": said.get("when", ""),
            "files": said.get("files") or [],
            "bytes": said.get("bytes") or sum(
                f.stat().st_size for f in room.rglob("*") if f.is_file()),
        })
    return out
