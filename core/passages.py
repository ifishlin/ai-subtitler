"""這個題目切出來的影片段落：切一次，存起來，兩邊讀同一份。

以前沒有這一支，段落是**現算的**，而且一輪裡算兩次 —— `brief.prompt()` 印
C1…C40 給模型看的時候算一次，`writer.fasten()` 把模型回答的 `C3` 換回檔案和
起訖的時候又算一次。兩次之間隔著模型回答的那幾十秒到幾分鐘。

它們一樣的唯一理由是「輸入沒變」。而輸入會變：`topic.keywords()` 讀的是影片
和報導的**標題**，所以中間只要有人按了「抓留言」「再收一批」，或補進一篇報導，
關鍵詞就變 → 命中數變 → 排序變 → **`C3` 指到另一段，而且不會報錯**。兩邊
各自都「正確地」算了一次。

情境影片踩過一模一樣的坑，修法是把抽出來的樣存進 `assets/broll/offered/`。
段落沒存，只是因為它是決定性的 —— 決定性不等於安全，只等於還沒發生。

存成一個題目一個檔，不塞進題目 JSON：段落連逐字稿大約二十 KB，而題目檔
是每次開頁面都要讀的東西。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core import rules as rules_module

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "assets" / "passages"


def path_for(name: str) -> Path:
    # 同一條名稱規則，問 topic 拿，不自己再寫一份 —— 兩份會分家，而分家的
    # 那一天是「這個題目存不進去」而不是報錯。
    from core import topic as topic_module
    if not topic_module.SAFE_NAME.fullmatch(name):
        raise ValueError("題目名稱只能用中英文、數字、底線、減號、空白")
    return HERE / f"{name}.json"


def choose(pile: dict[str, Any], want: float | None = None,
           per_video: int | None = None) -> list[dict[str, Any]]:
    """Every stretch of footage worth cutting, already on sentence boundaries.

    Offered as a list to pick from rather than as a rule to obey. A start and
    an end typed by hand land mid-sentence; these cannot, because they are the
    boundaries.

    ## 一支給幾段

    本來寫死 3 段。而這些影片實際上有 7、18、22、25、43 段 —— 第 4 段之後
    直接丟掉，**而且不出聲**。三段是一支七分鐘新聞的百分之十二，挑的人手上
    只有那一小撮。

    改成取前三分之一（比例和上限都在 `assets/rules.json` 的 `collect` 裡，
    不是寫死在這裡）。名次照 `moments()` 算的關鍵詞命中數，那個排序本來就在，
    只是以前只用來取前三名。

    上限存在是因為四十三段的三分之一還是十四段，而那一節會佔掉 prompt 的
    四分之一。段落是拿來挑的，多幾個選項有用；多到蓋過事實就沒用了。
    """
    from core import topic as topic_module
    words = topic_module.keywords(pile)
    share = rules_module.at("collect.passages_share", 0.34)
    most = rules_module.at("collect.passages_most", 12)
    everything = rules_module.at("collect.passages_all", 40)

    # 一支一支算它自己那三分之一。
    by_video: list[list[dict[str, Any]]] = []
    for video in pile["sources"]["videos"]:
        if not video.get("file"):
            continue
        # 先要全部，才知道三分之一是多少。`clip_passages` 本來就照命中數排，
        # 所以切前面那一段就是「最相關的那三分之一」。
        every = topic_module.clip_passages(video, words, want=want, most=999)
        if not every:
            continue
        # min 要包住 round，不是被 round 包住 —— 上限 12 的設定跑出過 13 段。
        room = per_video if per_video is not None else \
            max(1, min(most, round(len(every) * share)))
        # 兩件不同的事，兩個欄位：
        #   has_times  這份字幕檔有沒有寫每個字的時間（來源的性質）
        #   exact      這一段的切點有沒有真的用上真值（結果）
        #
        # 分開是因為它們會不一樣，而合成一個就是說謊。有時間戳但認不出是哪一
        # 個字的時候（`And`、`I` 這種常見字在附近出現好幾次），切點還是估的
        # —— 而第一版把整份檔案的性質當成每一段的性質，於是 C27 掛著「切點查
        # 真值」，實際上跳到錯的字上，比誠實地用估算更糟。
        from core import captions as captions_module
        from core import facts as facts_module
        has_times = captions_module.has_word_times(facts_module.captions_of(video))
        by_video.append([{**found, "file": video["file"],
                          "outlet": video.get("outlet", ""),
                          "has_times": has_times,
                          "title": video.get("title", "")[:70]}
                         for found in every[:room]])

    # 總數上限。十五支影片的三分之一是一百零二段，佔掉 prompt 的四成五，
    # 而一支片實際只用兩三段。
    #
    # 每支先保底一段，剩下的跨影片照命中數排名取 —— 只照分數排的話，
    # 一支四十三段的長片會把短片整支擠掉，而「這一家怎麼講」正是它的價值。
    kept = [rows[0] for rows in by_video]
    rest = sorted((one for rows in by_video for one in rows[1:]),
                  key=lambda one: -len(one.get("hits") or ()))
    kept += rest[:max(0, everything - len(kept))]
    # 排回原本的順序：同一支影片的段落要排在一起，時間也照著走。
    where = {id(one): (index, place)
             for index, rows in enumerate(by_video)
             for place, one in enumerate(rows)}
    kept.sort(key=lambda one: where.get(id(one), (99, 99)))

    # 最後一步：把切點吸附到聲音上。一支影片解碼一次音軌，套給它
    # 自己的那幾段 —— 放在挑完之後，是因為挑掉的那幾百段不用花這個時間。
    for film in {one["file"] for one in kept}:
        mine = [one for one in kept if one["file"] == film]
        settle(mine, envelope(ROOT / film))
    return kept


STEP = 0.02
# 量段落頭尾各多長，來判斷切點上還有沒有人在說話。
EDGE = 0.1


def _edge(clip: Path, at: float) -> float:
    """剪出來的檔案在 `at` 那 {EDGE} 秒的平均音量（dB）。

    完美的切點做不到 —— 有五段的那一秒鐘從頭到尾都在講話，沒有縫可以吸。
    做得到的是**把它們指出來**：切點上還有聲音，就是那一段值得有人打開來聽。
    """
    import re
    import subprocess
    done = subprocess.run(
        ["ffmpeg", "-v", "info", "-ss", f"{max(at, 0):.3f}", "-t", f"{EDGE}",
         "-i", str(clip), "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    found = re.search(r"mean_volume: (-?[\d.]+)", done.stderr)
    return round(float(found.group(1)), 1) if found else 0.0


def envelope(film: Path) -> list[float]:
    """這支影片的音量曲線，每 0.02 秒一個值（dB）。

    文字推得出「這句話大概第幾秒結束」，推不出「哪一秒沒有人在說話」，而剪
    出來聽起來對不對靠的正是後者。

    第一版問的是 ffmpeg 的 `silencedetect`：「哪幾段連續安靜超過 0.1 秒」。
    它救回了大部分，但**兩句之間的停頓常常不到 0.1 秒** —— C11 的結尾多出
    一個「Yeah.」，而正確的切點就在 0.06 秒外的一個 −44 dB 低谷上。那個谷
    在，只是不夠長，門檻式的偵測看不見它。

    改成拿整條曲線回來自己判斷。一樣是一次解碼，但「夠不夠安靜」可以就地跟
    周圍比，而不是跟一個寫死的絕對值比 —— 棚內訪談的底噪是 −55 dB，街訪是
    −30，同一個數字對前者太鬆、對後者太嚴。
    """
    import re
    import subprocess
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(film), "-vn", "-af",
         f"aresample=8000,asetnsamples={int(8000 * STEP)},"
         "astats=metadata=1:reset=1,"
         "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
         "-f", "null", "-"], capture_output=True, text=True)
    return [-120.0 if "inf" in one else float(one)
            for one in re.findall(r"RMS_level=(-?[\d.]+|-?inf)", done.stdout)]


def pause(levels: list[float], at: float, reach: float,
          drop: float) -> tuple[float, float] | None:
    """`at` 附近那個停頓（起, 訖），沒有就 None。

    停頓的定義是**相對的**：比這一秒鐘周圍的中位數安靜 drop 分貝以上。先找
    窗內最低的那一點，再往兩邊長到聲音回來為止。

    長度不設下限，因為需要的正是那些很短的停頓 —— C11 那個谷只有 0.06 秒，
    而它就是「Yeah.」前面唯一的縫。
    """
    first = max(0, int((at - reach) / STEP))
    last = min(len(levels), int((at + reach) / STEP))
    if last - first < 3:
        return None
    window = sorted(levels[first:last])
    floor = window[len(window) // 2] - drop
    low = min(range(first, last), key=lambda i: levels[i])
    if levels[low] > floor:
        return None                     # 這一秒鐘從頭到尾都有聲音
    head = tail = low
    while head > first and levels[head - 1] <= floor:
        head -= 1
    while tail + 1 < last and levels[tail + 1] <= floor:
        tail += 1
    # 一個取樣點不是停頓，是雜訊。長度下限本來完全沒有 —— 為了接住 C11 那個
    # 0.06 秒的縫我把它整個拿掉，於是 C9 的結尾「找到」一個 0.02 秒的尖點，
    # 那裡從頭到尾都在講話。下限訂在還接得住 C11 的地方。
    if (tail + 1 - head) * STEP < rules_module.at("collect.passage_gap", 0.06):
        return None
    return head * STEP, (tail + 1) * STEP


def settle(rows: list[dict[str, Any]], levels: list[float],
           reach: float = 1.0) -> int:
    """把切點吸附到聲音上。回傳動到幾個。

    一條規則，套在每一段上，不是針對哪一段微調：

        頭   在說話開始前 lead 秒
        尾   在說話停止後 tail 秒

    兩端都用同一個錨點 —— 一個停頓的**結束**就是「說話從這裡開始」。

    找不到停頓就不動。新聞畫面有配樂和連續問答，不是每一句之間都停得下來，
    而**動不了的時候維持原樣，比硬吸到一秒外的某個地方好** —— 後者會把切點
    移到另一句話上。
    """
    if not levels:
        return 0
    lead = rules_module.at("collect.passage_lead", 0.15)
    tail = rules_module.at("collect.passage_tail", 0.30)
    drop = rules_module.at("collect.passage_drop", 10)
    moved = 0
    for one in rows:
        was = (one["start"], one["end"])
        found = pause(levels, one["start"], reach, drop)
        if found:
            one["start"] = round(max(found[0], found[1] - lead), 2)
        found = pause(levels, one["end"], reach, drop)
        if found:
            one["end"] = round(min(found[1], found[0] + tail), 2)
        if one["end"] - one["start"] < 1.0:      # 吸壞了就退回去
            one["start"], one["end"] = was
            continue
        one["seconds"] = round(one["end"] - one["start"], 2)
        one["settled"] = True
        moved += (one["start"], one["end"]) != was
    return moved


def cut(name: str) -> dict[str, Any]:
    """切這個題目的段落，寫進檔案，回報切出幾段。

    每次收集完、每次補下載完都跑一次，因為那兩件事都會改變手上有哪些影片和
    哪些關鍵詞。破壞性的動作放最後：算完才寫，中途失敗只損失時間，舊的那份
    還在。
    """
    from core import topic as topic_module
    pile = topic_module.load(name)
    with_captions = [one for one in topic_module.settled(pile, "videos")
                     if one.get("file") and one.get("captions")]
    # 沒有素材就不要留下檔案。`stored()` 的規矩是「沒有就現切」，而一個還沒
    # 收過素材的題目被誰開了一次頁面，就會長出一個 0 段的空檔 —— 那個檔進了
    # 版控，看起來像「這個題目切過了，結果是零段」，而實際上是「還沒收素材」。
    # 兩件事在畫面上一模一樣，而後者才是真的。
    if not with_captions:
        path = path_for(name)
        if path.is_file():
            path.unlink()
        return {"topic": name, "when": int(time.time()), "videos": 0,
                "silent": [], "passages": []}
    rows = choose(pile)
    HERE.mkdir(parents=True, exist_ok=True)
    body = {
        "topic": name,
        "when": int(time.time()),
        "videos": len(with_captions),
        # 讀了 N 支影片、切出 0 段要看得見。「找到 0 筆」和「沒有去找」在畫面
        # 上一模一樣，而後者才是錯 —— 這個題目所有的影片段落都從這裡來。
        "silent": [one.get("outlet", "") for one in with_captions
                   if not any(row["file"] == one["file"] for row in rows)],
        "passages": rows,
    }
    path_for(name).write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return body


def carve(name: str, say=None) -> dict[str, Any]:
    """真的把每一段剪成一個檔案，放在 `assets/passages/<題目>/`。

    存起訖秒數就夠讓流水線跑，但**不夠讓人看**。網頁本來是叫瀏覽器跳到原片
    的那一秒播 —— 那看到的是「我以為我切在這裡」，不是切出來的東西。而這個
    專案的錯有一半是「程式跑完了，只是畫面上不是那樣」。

    檔名帶起訖的指紋。改了切法就換檔名，舊的檔案自然不會被當成新的 ——
    這是卡片和鏡頭已經在用的做法。少了它，第二次切完看到的還是第一次的畫面，
    而且看起來完全正常。

    重編碼而不是 `-c copy`：串流複製只能切在關鍵影格上，而關鍵影格之間隔著
    好幾秒 —— 整支的重點就是切在句子的邊界，切完卻對不上，那等於沒做。

    破壞性的動作放最後：全部剪完，才掃掉沒有人指著的舊檔。中途失敗只損失
    時間。
    """
    import hashlib
    import subprocess
    body = report(name)
    rows = body.get("passages") or []
    room = HERE / name
    room.mkdir(parents=True, exist_ok=True)

    made, failed = [], []
    for index, one in enumerate(rows, start=1):
        mark = hashlib.sha1(
            f"{one['file']}|{one['start']}|{one['end']}".encode("utf-8")
        ).hexdigest()[:10]
        cut_to = room / f"C{index:02d}.{mark}.mp4"
        if not cut_to.is_file():
            done = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{one['start']:.3f}",
                 "-to", f"{one['end']:.3f}", "-i", str(ROOT / one["file"]),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                 str(cut_to), "-y"], capture_output=True, text=True)
            if done.returncode or not cut_to.is_file():
                failed.append(f"C{index}：{done.stderr.strip()[:80]}")
                continue
        one["clip"] = str(cut_to.relative_to(ROOT))
        made.append(cut_to.name)
        # 一張縮圖。清單是拿來找的，而「這一段長什麼樣」用讀的比用點的快 ——
        # 四十段逐一按下去播要好幾分鐘，掃一遍縮圖只要幾秒。
        #
        # 取四成處而不是第一格：第一格常常還在上一個鏡頭的轉場上，那是黑的
        # 或糊的。卡片頁的六十格全黑就是同一種錯的另一個版本。
        card = cut_to.with_suffix(".jpg")
        if not card.is_file():
            subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{one['seconds'] * 0.4:.2f}",
                 "-i", str(cut_to), "-frames:v", "1", "-vf", "scale=360:-2",
                 "-q:v", "4", str(card), "-y"], capture_output=True)
        if card.is_file():
            one["thumb"] = str(card.relative_to(ROOT))
            made.append(card.name)
        # 剪完之後量自己的頭尾還有沒有聲音。**量剪出來的檔案，不是量原片** ——
        # 量原片要跨著切點取樣，而一個切得剛好、尾巴緊貼下一句的段落也會量到
        # 很大聲。我用那把尺誤判過 C11：它其實是對的。
        one["edge"] = [_edge(cut_to, 0.0), _edge(cut_to, one["seconds"] - EDGE)]
        # 程式判斷得了「切點上有沒有聲音」，判斷不了「這樣剪聽起來能不能接受」。
        # 所以這是一個記號不是一道門 —— 它該做的是把要看的挑出來，不是替人決定。
        one["doubt"] = any(db > rules_module.at("collect.passage_edge", -30)
                           for db in one["edge"])
        if say:
            say(index, len(rows), f"剪 C{index}　{one['outlet']}")

    # 數段落，不是數檔案。`made` 現在一段放兩個名字（mp4 和縮圖），拿它的
    # 長度當「剪出幾段」就會在畫面上寫 80 —— 而 80 看起來完全像一個真的數字。
    body["carved"] = sum(1 for one in rows if one.get("clip"))
    body["failed"] = failed
    path_for(name).write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    # 掃掉沒有人指著的舊檔 —— 最後一步，而且只在新的已經收齊之後。
    keep = set(made)
    gone = [one for one in room.iterdir()
            if one.suffix in (".mp4", ".jpg") and one.name not in keep]
    if gone:
        from core import bin as bin_module
        bin_module.toss([str(one.relative_to(ROOT)) for one in gone],
                        f"換掉的影片段落：{name}")
    body["swept"] = len(gone)
    return body


def stored(name: str) -> list[dict[str, Any]]:
    """這個題目存好的段落。沒有就現切一份 —— 不回空清單。

    回空清單的話，一個從來沒切過的題目在 prompt 裡就是「## 影片段落」底下
    什麼都沒有，而模型會照著寫出一支沒有任何動態畫面的片，每一道門都過。
    """
    path = path_for(name)
    if not path.is_file():
        return cut(name)["passages"]
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("passages") or []
    except json.JSONDecodeError:
        return cut(name)["passages"]


def report(name: str) -> dict[str, Any]:
    """整份紀錄，給網頁看。沒切過的先切。"""
    path = path_for(name)
    if not path.is_file():
        return cut(name)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return cut(name)
