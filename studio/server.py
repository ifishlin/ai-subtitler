"""Local subtitle proofreading server.

Read-only with respect to the pipeline: it reads output/ and work/, and writes
only output/subtitles_zh.reviewed.srt, output/final_reviewed.mp4 and
editor_cache/. Nothing in produce.py or core/ is modified or re-run.

    .venv/bin/python studio/server.py
    open http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import inspect                                                    # noqa: E402
import json                                                       # noqa: E402

from fastapi import Body, FastAPI, HTTPException                      # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles                             # noqa: E402
from opencc import OpenCC                                            # noqa: E402
from PIL import Image                                               # noqa: E402

from studio import media, review                            # noqa: E402
from studio.srt import write_srt                            # noqa: E402

WORK = ROOT / "work"
# Every run lives in here, one directory each. What makes a directory a project
# is that it holds a run.json saying which video it is about -- not what it is
# called. A name is for people; the marker is for the program.
PROJECTS = ROOT / "projects"
# Everything here is regenerable, but it accumulates over months of runs, so
# each kind of file gets its own drawer rather than one flat heap.
CACHE = ROOT / "editor_cache"
PROXY = CACHE / "proxy"              # browser-playable copies of the sources
WAVEFORM = CACHE / "waveform"        # peak summaries for the timeline
REVIEWS = CACHE / "review"           # a session's confirmations, per run
CAPTIONS = CACHE / "captions"        # drawn captions, one directory per run
PREVIEWS = CACHE / "preview"         # short test burns and burn progress
FILMSTRIP = CACHE / "filmstrip"      # one tiled image of thumbnails per source
SCRATCH = CACHE / "scratch"          # anything written by hand while working
STATIC = Path(__file__).resolve().parent / "static"

# Everything that depends on which run is being reviewed lives in config, so
# the browser can switch runs without restarting the server. Media derivatives
# are keyed by video, so switching back to a run costs nothing the second time.
# Everything placeable, in one place. Four top-level directories for four
# kinds of the same thing meant the shelf was scattered on disk even after the
# library brought it together on screen.
ASSETS = ROOT / "assets"
IMAGE_DIRS = ("assets/cutouts", "assets/images")   # cut-outs preferred
CLIP_DIR = "assets/clips"           # footage to lay over the frame
CARDS_TRIMMED = 2                   # scenes carrying cards cropped to their art


def cache_key(source: Path) -> str:
    """A name unique to this file as it is right now.

    Every assembly is called assembled.mp4, so the stem alone had two different
    runs share one proxy, one waveform and one filmstrip -- and re-assembling
    under the same name left the timeline drawn from the previous video, which
    is what a broken preview looks like. Size and modification time make a
    rebuilt file a different key, so nothing stale is ever reused.
    """
    try:
        stat = source.stat()
        mark = f"{source.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    except OSError:
        mark = str(source)
    return f"{source.stem}-{hashlib.sha1(mark.encode()).hexdigest()[:8]}"


def paths_for(output: Path, source: Path | None = None) -> dict[str, Path]:
    stem = cache_key(source) if source else "none"
    return {
        "output": output,
        "state": REVIEWS / f"{output.name}.json",
        "reviewed_srt": output / "subtitles_zh.reviewed.srt",
        "reviewed_mp4": output / "final_reviewed.mp4",
        "proxy": PROXY / f"{stem}.mp4",
        "peaks": WAVEFORM / f"{stem}.json",
        "film": FILMSTRIP / f"{stem}.png",
        "scene": output / "scene.json",
        "burn_progress": PREVIEWS / f"{output.name}.progress",
        "captions": CAPTIONS / output.name,
        "preview": PREVIEWS / f"{output.name}.mp4",
        "preview_clip": PREVIEWS / f"{output.name}_clip.mp4",
    }

# Swagger moves out of the way. FastAPI claims /docs for its own API browser
# and claimed it first, so the project's own documentation was served the
# OpenAPI page instead -- a route that looks registered and is shadowed.
app = FastAPI(title="影片流水線", docs_url="/api-browser", redoc_url=None)
# Shared between the four pages. Everything else here is served by hand, but
# the dialog is one file used four times and copying it four times is how four
# slightly different dialogs happen.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"),
          name="static")
config: dict[str, Any] = {}
_whisper_models: dict[str, Any] = {}
_burn: dict[str, Any] = {"state": "idle", "message": "", "output": None,
                         "percent": 0, "seconds": 0.0, "started": 0.0}
_burn_lock = threading.Lock()


def _frame_rate(video: Path) -> float:
    """Frames per second, so the editor can step one frame at a time."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(video)],
        check=False, capture_output=True, text=True,
    )
    numerator, _, denominator = result.stdout.strip().partition("/")
    try:
        return round(float(numerator) / float(denominator or 1), 3)
    except ValueError:
        return 30.0


def list_projects() -> list[dict[str, Any]]:
    """Every pipeline output directory that holds subtitles to review."""
    found = []
    if not PROJECTS.is_dir():
        return []
    for directory in sorted(PROJECTS.iterdir()):
        run = directory / "run.json"
        if not (directory.is_dir() and run.is_file()):
            continue
        srt = directory / "subtitles_zh.srt"
        if not srt.is_file():                       # a run with nothing to show
            srt.write_text("", encoding="utf-8")
        details = json.loads(run.read_text(encoding="utf-8"))
        source = Path(details.get("source", ""))
        found.append({
            "name": directory.name,
            "lines": srt.read_text(encoding="utf-8").count("-->"),
            "source": source.name,
            "seconds": round(media.duration(source), 2) if source.is_file() else 0.0,
            "assembled": bool(details.get("assembled")),
            "size": sum(item.stat().st_size for item in directory.rglob("*")
                        if item.is_file()),
            # How many lines the second listening pass actually recovered.
            # Whether it ran is not worth saying: it runs every time.
            "recovered": int(details.get("recovered_segments") or 0),
            "reviewed": (directory / "subtitles_zh.reviewed.srt").is_file(),
            "modified": int(srt.stat().st_mtime),
        })
    return found


def find_source(output: Path) -> Path:
    """The video this pipeline run used, or the newest download as a fallback."""
    recorded = output / "run.json"
    if recorded.is_file():
        source = Path(json.loads(recorded.read_text(encoding="utf-8")).get("source", ""))
        if source.is_file():
            return source
        # A project that moved carries its own video with it, so a recorded path
        # that no longer exists is usually the same file one directory over.
        beside = output / source.name
        if source.name and beside.is_file():
            return beside
        if source.name:
            raise HTTPException(
                409,
                f"{output.name}/run.json 說影片是 {source}，但那裡沒有檔案。"
                f"請把 run.json 的 source 改成現在的位置。")
    candidates = sorted(WORK.glob("source_*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit(f"在 {WORK} 找不到來源影片，請用 server.py /path/to/video.mp4 指定")
    if len(candidates) > 1:
        # Guessing pairs one run's subtitles with another run's picture, which
        # looks like corrupted output rather than a missing record.
        names = "、".join(item.name for item in candidates)
        raise HTTPException(
            409,
            f"{output.name} 沒有 run.json，無法判斷它用的是哪支影片。"
            f"work/ 裡有 {names}。請重跑 produce.py，或手動建立 "
            f"{output.name}/run.json 寫入 {{\"source\": \"work/影片檔名.mp4\"}}。",
        )
    print(f"提醒：{output.name}/run.json 不存在，只有一支影片，使用 {candidates[0].name}")
    return candidates[0]


# ---------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def home() -> str:
    """The way in.

    `/` used to be the AI-Desk itself, and no page linked to any other -- to
    change service you edited the address bar, which means anyone who does not
    already know the paths cannot get there. The desk is still one of the
    services and now lives at /desk.
    """
    return (STATIC / "home.html").read_text(encoding="utf-8")


@app.get("/desk", response_class=HTMLResponse)
def desk() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/topics", response_class=HTMLResponse)
def topics_page() -> str:
    """Gathering: the pile, and whether it has heard both sides.

    Split from /scripts, which was 86 KB doing two jobs with different
    lifetimes -- a pile is gathered once and then mostly left alone, a script
    is rewritten over and over, and one topic feeds several scripts.
    """
    return (STATIC / "topics.html").read_text(encoding="utf-8")


@app.get("/gates", response_class=HTMLResponse)
def gates_page() -> str:
    return (STATIC / "gates.html").read_text(encoding="utf-8")


@app.get("/docs", response_class=HTMLResponse)
def docs_page() -> str:
    return (STATIC / "docs.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 門與文件
#
# Both read their source at request time rather than being written out. A page
# that describes the checks is worthless the moment it disagrees with them,
# and this project has already shipped two copies of one rule that drifted.


@app.get("/api/gates")
def list_gates() -> dict[str, Any]:
    """Every gate, its docstring, and how each script stands against it.

    Assembled from `script.GATES` and the functions themselves, so a gate
    added to the code appears here without anyone editing a page, and a
    docstring that explains which mistake it was built for is the description.
    """
    from core import script as script_module
    from core import topic as topic_module

    def spelled(fn) -> str:
        return inspect.getdoc(fn) or ""

    listed = script_module.listing()
    filed_by = {one["name"]: one["topic"] for one in listed}
    filed_scripts = {one["name"] for one in listed if one["archived"]}

    scripts = []
    for name in script_module.names():
        try:
            measured = script_module.measure(script_module.load(name))
        except Exception:                                         # noqa: BLE001
            continue
        scripts.append({
            "name": name,
            # Which topic, and whether that topic is put away. Derived in
            # script.listing() rather than stored, so the page cannot disagree
            # with the list about what is archived.
            "topic": filed_by.get(name, ""),
            "archived": name in filed_scripts,
            "faults": {one[0]: len(measured.get(one[0]) or [])
                       for one in script_module.GATES},
            "sums": {"over": measured["over"],
                     # The verdict, not the number, so the page cannot decide
                     # differently from the builder. It did: a shipped 90.01s
                     # film showed as too long here and had been accepted there.
                     "too_long": script_module.runs_over(measured),
                     "even": measured["even"],
                     "still_enough": measured["still_enough"]},
        })

    gates = []
    for key, label, kind, blocks, why in script_module.GATES:
        # Two gates are not functions of their own name: `shapeless` is what
        # `structure()` returns and `unsourced` is computed inside measure().
        # Named here rather than left blank, because a gate with no
        # explanation is the one nobody understands well enough to trust.
        fn = getattr(script_module,
                     {"unpicked": "missing_pictures",
                      "shapeless": "structure",
                      "unsourced": "measure"}.get(key, key), None)
        gates.append({"key": key, "label": label, "kind": kind,
                      "blocks": blocks, "why": why,
                      "doc": spelled(fn) if fn else "",
                      "hits": sum(one["faults"].get(key, 0) for one in scripts)})

    sums = [{"key": key, "label": label, "kind": kind, "why": why}
            for key, label, kind, why in script_module.SUMS]

    piles = []
    for name in topic_module.names():
        try:
            pile = topic_module.load(name)
        except Exception:                                         # noqa: BLE001
            continue
        enough, missing = topic_module.ready(pile)
        piles.append({"name": name, "ready": enough, "why": missing})

    return {
        "gates": gates, "sums": sums, "scripts": scripts, "piles": piles,
        "kinds": {
            "frame": ["會出現在畫面上的東西",
                      "測的就是觀眾會看到的那個東西本身，中間沒有人。最紮實的一類"],
            "script": ["文案這個資料結構自己",
                       "只看那一個檔案就能判，跟畫面和素材都無關"],
            "join": ["文案和素材堆之間的接縫",
                     "兩份資料的 join。抓到過把別的題目的照片寫進來 —— 檔案確實存在"],
            "declared": ["一個宣告，不是東西本身",
                         "它測不到那張圖對不對，只測有沒有人聲稱看過。"
                         "說謊就會過 —— 這一類明顯弱於其他三類"],
            "sequence": ["成片是一連串鏡頭",
                         "唯一不是逐句判斷的一類。每張單獨看都好，排在一起是投影片"],
        },
        # The numbers the gates compare against. Straight from the file the
        # checks and the prompts both read, so the page cannot quote a stale
        # threshold.
        "thresholds": json.loads(
            (ROOT / "assets" / "rules.json").read_text(encoding="utf-8")),
        "extra": [
            {"key": "ready", "label": "素材夠不夠", "kind": "join", "blocks": True,
             "where": "core/topic.py　ready()",
             "why": "影片 5、報導 5、三種圖各 5，而且左中右都要有。"
                    "「左中右都要有」是這個專案唯一一道測觀點平衡的門",
             "doc": spelled(topic_module.ready)},
            {"key": "checkjs", "label": "網頁的 JavaScript 語法",
             "kind": "script", "blocks": True,
             "where": "studio/checkjs.sh　+ .git/hooks/pre-commit",
             "why": "三個 handler 忘了寫 async，整頁空白而伺服器回 200。"
                    "commit 時自動跑，--no-verify 可以跳過",
             "doc": ""},
        ],
    }


DOCS = {
    "MISTAKES": "犯過的錯",
    "TESTED": "測到哪裡",
    "USING": "怎麼用",
    "PROGRESS": "進度",
    "COLLECTING": "怎麼收集",
}


@app.get("/api/doc")
def read_doc(name: str = "MISTAKES") -> dict[str, Any]:
    """One of docs/*.md, rendered. Whitelisted, and read fresh every time."""
    if name not in DOCS:
        raise HTTPException(404, f"沒有這份文件：{name}")
    import markdown as markdown_module
    path = ROOT / "docs" / f"{name}.md"
    if not path.is_file():
        raise HTTPException(404, f"找不到 docs/{name}.md")
    body = path.read_text(encoding="utf-8")
    return {
        "name": name, "title": DOCS[name],
        "html": markdown_module.markdown(
            body, extensions=["tables", "fenced_code", "toc", "sane_lists"]),
        "words": len(body),
        "changed": int(path.stat().st_mtime),
        "all": [{"name": key, "title": label} for key, label in DOCS.items()
                if (ROOT / "docs" / f"{key}.md").is_file()],
    }


@app.get("/media/proxy.mp4")
def proxy() -> FileResponse:
    # FileResponse honours HTTP Range, which is what lets the browser seek.
    # Every run answers on this one path, so without no-store the browser keeps
    # serving whichever video it cached first.
    return FileResponse(
        config["paths"]["proxy"], media_type="video/mp4",
        headers={"Cache-Control": "no-store"},
    )


def finished_videos() -> list[dict[str, Any]]:
    """Burned videos in this run's directory, newest first."""
    output = config["paths"]["output"]
    found = [
        {"name": item.name, "size": item.stat().st_size, "modified": int(item.stat().st_mtime)}
        for item in sorted(output.glob("*.mp4"))
    ]
    return sorted(found, key=lambda item: -item["modified"])


@app.get("/media/final/{name}")
def final_video(name: str) -> FileResponse:
    """Serve a burned video for playback. Names are matched against the
    directory listing rather than joined, so no path can escape it."""
    output = config["paths"]["output"]
    if name not in {item["name"] for item in finished_videos()}:
        raise HTTPException(404, f"找不到 {name}")
    return FileResponse(output / name, media_type="video/mp4",
                        headers={"Cache-Control": "no-store"})


_assembly: dict[str, Any] = {"state": "idle", "message": "", "output": None}
_assembly_lock = threading.Lock()


def _opening_line(srt: Path | None) -> str:
    """The first thing said. A directory name says when a run was made; this
    says what is in it, which is what anyone is actually looking for."""
    if not srt or not srt.is_file():
        return ""
    for block in srt.read_text(encoding="utf-8").split("\n\n")[:4]:
        said = [line.strip() for line in block.splitlines()
                if line.strip() and "-->" not in line and not line.strip().isdigit()]
        if said:
            return said[0][:40]
    return ""


@app.get("/api/sources")
def sources() -> dict[str, Any]:
    """Everything that can become a piece: finished runs, and raw footage.

    A run brings its subtitles with it, which is what makes joining two
    interviews possible without transcribing anything again.
    """
    found = []
    for project in list_projects():
        directory = PROJECTS / project["name"]
        run = directory / "run.json"
        details = json.loads(run.read_text(encoding="utf-8")) if run.is_file() else {}
        source = Path(details.get("source") or "")
        if not source.is_file():
            continue
        srt = next((directory / name for name in
                    ("subtitles_bilingual.reviewed.srt", "subtitles_bilingual.srt",
                     "subtitles_zh.reviewed.srt", "subtitles_zh.srt")
                    if (directory / name).is_file()), None)
        found.append({
            "kind": "run", "name": project["name"],
            "source": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
            "srt": str(srt.relative_to(ROOT)) if srt else None,
            "seconds": round(media.duration(source), 2),
            "lines": project["lines"],
            "opens": _opening_line(srt),
        })
    for clip in _clips():
        found.append({
            "kind": "clip", "name": clip["name"], "source": clip["path"],
            "srt": None, "seconds": clip["seconds"], "lines": 0,
        })
    return {"sources": found}


TRASH = ROOT / "trash"


@app.post("/api/source/remove")
def remove_source(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Take a source out of the list.

    Moved into trash/ rather than deleted: a run is a night of transcribing and
    a clip is a download, and the list is one careless click wide. Emptying
    trash/ is a decision made deliberately, elsewhere.
    """
    name = str(payload.get("name") or "")
    offered = {item["name"]: item for item in sources()["sources"]}
    if name not in offered:
        raise HTTPException(404, f"找不到片源 {name}")
    if name == config["paths"]["output"].name:
        raise HTTPException(400, f"{name} 正在編輯器裡開著，先切到別的專案再刪")

    kind = offered[name]["kind"]
    going = project_dir(name) if kind == "run" else (ROOT / offered[name]["source"])
    if not going.exists():
        raise HTTPException(404, f"{going.name} 已經不在了")

    return {"removed": name, "at": str(_to_trash(going).relative_to(ROOT))}


SAFE_NAME = re.compile(r"[\w\u4e00-\u9fff][\w\u4e00-\u9fff -]{0,63}")


def project_dir(name: str) -> Path:
    """Where a project of this name lives. The name is checked rather than
    joined blindly: it becomes a directory, so it may not climb out of one."""
    if not SAFE_NAME.fullmatch(name):
        raise HTTPException(400, "名稱只能用中英文、數字、底線、減號、空白，不能開頭是符號")
    return PROJECTS / name


def _to_trash(going: Path) -> Path:
    """Move something out of the way rather than destroy it.

    A run is a night of transcribing and a clip is a download; a mistaken click
    should cost a move, not the work. Emptying trash/ is a separate decision,
    made deliberately and elsewhere.
    """
    import shutil
    TRASH.mkdir(parents=True, exist_ok=True)
    # Stamped, so removing two runs of the same name a week apart keeps both.
    landed = TRASH / f"{time.strftime('%Y%m%d-%H%M%S')}-{going.name}"
    shutil.move(str(going), str(landed))
    return landed


# ---------------------------------------------------------------- stage one
#
# Making the video, as opposed to mending it. The page this serves has one
# field, one button and a progress line, and deliberately nothing else: the
# moment it starts asking which card to use or where to put it, that is a
# decision the pipeline failed to make, and the fix belongs in produce.py.

# ------------------------------------------------------------------ jobs
#
# Gathering and rendering both take minutes, and both were things I ran on the
# command line -- which meant the page could not do half of what the pipeline
# does, and anybody but me could not do it at all. One runner for both: a job
# says what it is doing and how far along, the page asks, and only one runs at
# a time because both saturate the machine.

_job: dict[str, Any] = {"state": "idle", "what": "", "step": 0, "steps": 0,
                        "note": "", "topic": "", "started": 0.0, "log": []}
_job_lock = threading.Lock()


def _job_say(step: int, steps: int, note: str) -> None:
    with _job_lock:
        _job.update(step=step, steps=steps, note=note)
        _job["log"] = (_job["log"] + [note])[-40:]


def _job_run(what: str, topic: str, work) -> None:
    """Run one long task in the background, reporting as it goes."""
    with _job_lock:
        if _job["state"] == "running":
            raise HTTPException(409, f"還在做「{_job['what']}」，等它跑完")
        _job.update(state="running", what=what, topic=topic, step=0, steps=0,
                    note="開始", started=time.time(), log=[])

    def go() -> None:
        try:
            work(_job_say)
            with _job_lock:
                _job.update(state="done", note="完成")
        except Exception as error:                                # noqa: BLE001
            with _job_lock:
                _job.update(state="failed", note=str(error))

    threading.Thread(target=go, daemon=True).start()


@app.get("/api/job")
def get_job() -> dict[str, Any]:
    with _job_lock:
        return {**_job, "seconds": round(time.time() - _job["started"], 1)
                        if _job["started"] else 0.0}


STEP_LINE = re.compile(r"^\[(\d)/(\d)\]\s*(.*)")
_produce: dict[str, Any] = {"state": "idle", "step": 0, "steps": 8, "what": "",
                            "message": "", "warning": "", "project": None,
                            "started": 0.0, "log": []}
_produce_lock = threading.Lock()
_produce_process: dict[str, Any] = {"handle": None, "stopping": False}


def _sites() -> list[str]:
    """Every site yt-dlp can fetch from. Asked once -- it is a fixed list for a
    given version, and enumerating it takes a second."""
    if not hasattr(_sites, "cached"):
        found = subprocess.run([str(ROOT / ".venv/bin/yt-dlp"), "--list-extractors"],
                               capture_output=True, text=True)
        _sites.cached = sorted({line.strip() for line in found.stdout.splitlines()
                                if line.strip() and ":" not in line})
    return _sites.cached


# ------------------------------------------------------------------ topics

@app.get("/api/topics")
def get_topics() -> dict[str, Any]:
    from core import topic as topic_module
    return {"topics": topic_module.listing(),
            "want": topic_module.WANT,
            "media": topic_module.media()}


@app.get("/api/topic")
def get_topic(name: str) -> dict[str, Any]:
    """A topic with the reading done on it: what is there, what is missing,
    and whether anyone who disagrees has been heard."""
    from core import topic as topic_module
    try:
        pile = topic_module.load(name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    from core import script as script_module
    enough, why = topic_module.ready(pile)
    return {**pile, "counts": topic_module.counts(pile),
            "balance": topic_module.balance(pile), "ready": enough, "why": why,
            # Read off the scripts, not out of the topic file: the copy stored
            # there was never written to, so finished scripts did not appear.
            "scripts": [one for one in script_module.listing()
                        if one["topic"] == name],
            "audience": topic_module.audience(pile),
            # Offered, not shown as though it were the answer.
            "audience_guess": topic_module.suggest_audience(pile),
            # Pictures the audience implies and the pile does not have. Counts
            # cannot see this: thirty pictures of studios satisfy "15 photos"
            # while the film has nothing to end on.
            "wanted": topic_module.wanted_shots(pile),
            # Kept, but flagged. A decision waiting for somebody, not a
            # deletion already carried out on a model's say-so.
            "doubted": topic_module.doubted(pile)}


@app.post("/api/topic")
def new_topic(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from core import topic as topic_module
    name = str(payload.get("name") or "").strip()
    try:
        path = topic_module.path_for(name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if path.is_file():
        raise HTTPException(400, f"{name} 已經存在了")
    pile = topic_module.blank(name, str(payload.get("note") or ""))
    topic_module.save(name, pile)
    return {"made": name, "topics": topic_module.listing()}


@app.post("/api/topic/terms")
def suggest_terms(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Suggest what to search for, from the topic and who it is for.

    Quick enough to wait for -- one short answer rather than a whole script --
    so it returns the terms instead of becoming a job. They are put in the box
    rather than used: a suggestion you cannot see before it runs is a decision
    somebody else made.
    """
    from core import writer as writer_module
    name = str(payload.get("name") or "")
    reachable, complaint = _model_reachable()
    if not reachable:
        raise HTTPException(503, f"{complaint}　想搜尋詞需要它。")
    try:
        return writer_module.suggest_terms(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(400, str(error)) from error


@app.delete("/api/topic")
def drop_topic(name: str) -> dict[str, Any]:
    """Remove a topic, everything gathered for it, and everything made from it.

    It used to refuse while a script existed and say 「先刪文案」. That was the
    right instinct -- a script names pictures by path, so taking the pile away
    leaves it pointing at files that are gone -- but the wrong conclusion:
    deleting one topic became several deletions in an order nobody was told,
    and the pieces it did remove (footage, photographs) were exactly the ones
    the remaining scripts still needed.

    So it takes the lot, and says what the lot was. `everything_for()` derives
    the paths from each module's own constant, because I did this by hand once
    and guessed three of eight wrong -- 14 MB stayed behind and nothing said so.

    Moved, not deleted, and the tree under trash/ keeps each file's own path,
    so putting something back is a move rather than a reconstruction.
    """
    import shutil
    import time as time_module
    from core import script as script_module
    from core import topic as topic_module
    try:
        path = topic_module.path_for(name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not path.is_file():
        raise HTTPException(404, f"找不到題目 {name}")

    # Everything, in one place, from each module's own constant. It used to
    # refuse while a script existed and told you to delete those first, which
    # meant deleting one topic was several deletions in a required order and
    # nobody was told the order.
    written = script_module.for_topic(name)
    owned = topic_module.everything_for(name)
    bytes_going = topic_module.weight(owned)

    # The destructive step goes last. A directory is moved, not removed: the
    # tree under trash/ keeps each file's own path, so putting one back is a
    # move rather than a reconstruction. A failure halfway costs the rest of
    # the move, never the files themselves.
    stamp = time_module.strftime("%Y%m%d-%H%M%S")
    bin_here = ROOT / "trash" / f"{name}-{stamp}"
    gone = []
    for one in owned:
        where = bin_here / one.relative_to(ROOT)
        where.parent.mkdir(parents=True, exist_ok=True)
        size = topic_module.weight([one])
        shutil.move(str(one), str(where))
        gone.append({"path": str(one.relative_to(ROOT)), "bytes": size})

    return {"deleted": name, "moved_to": str(bin_here.relative_to(ROOT)),
            "scripts": written,
            "items": gone, "bytes": bytes_going}


@app.post("/api/topic/gather")
def gather(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """The whole gathering round, as one job.

    Ask each outlet, download what they gave, cut frames where the captions
    say to. All three ran only from the command line, which meant the page
    could not do the part of this project that its whole argument rests on.
    """
    from core import stock as stock_module
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    queries = [one.strip() for one in (payload.get("queries") or "").split(",")
               if one.strip()]
    if not queries:
        raise HTTPException(400, "要給搜尋詞（英文，逗號分隔）")
    try:
        topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error

    shots = [one.strip() for one in (payload.get("pictures") or "").split(",")
             if one.strip()]
    named = [one.strip() for one in (payload.get("named") or "").split(",")
             if one.strip()]

    def work(say) -> None:
        from core import stock as stock_module
        from core import writer as writer_module

        # Warnings live on the pile, not only in the job log. The log is in
        # memory and dies with a restart: a round that never judged relevance
        # said so once, into a buffer that was gone ten minutes later, and the
        # topic then looked exactly like one that had been judged clean.
        trouble: list[str] = []

        # 1. What each outlet broadcast -- and whether it is about this.
        # Videos were never sifted, only reports, so a search for "Ellison"
        # filed a segment about the DNC's Keith Ellison under a story about
        # Larry Ellison, and it voted in the balance like any other source.
        found = topic_module.hunt(name, queries, say=say)
        try:
            found = writer_module.sift(name, found, say)
        except Exception as error:                                # noqa: BLE001
            trouble.append(f"影片沒判斷相關性（{error}）")
            say(0, 1, f"⚠ 影片沒判斷相關性（{error}），全部留著")
        pile = topic_module.load(name)
        pile["sources"]["videos"] = pile["sources"]["videos"] + found
        topic_module.save(name, pile)

        # 2. What each outlet wrote. The half that had no code at all, so the
        # balance check could never pass without somebody typing reports in.
        wrote = topic_module.hunt_reports(name, queries, say=say)
        try:
            wrote = writer_module.sift(name, wrote, say)
        except Exception as error:                                # noqa: BLE001
            # Kept, but said so. Swallowing this is how twenty-eight reports
            # including an airport malaria story were filed as relevant while
            # the job reported 完成.
            trouble.append(f"報導沒判斷相關性（{error}）")
            say(0, 1, f"⚠ 沒判斷相關性（{error}），全部留著")
        pile = topic_module.load(name)
        pile["sources"]["reports"] = (pile["sources"].get("reports") or []) + wrote
        topic_module.save(name, pile)

        # Download only what the balance actually needs, newest search first.
        want = topic_module.WANT["videos"]
        todo = [v for v in pile["sources"]["videos"] if not v.get("file")][:want]
        for index, video in enumerate(todo, start=1):
            say(index, len(todo), f"下載 {video.get('outlet', '')}")
            got = topic_module.bring_in(name, video)
            if got:
                video.update(got)
                topic_module.save(name, pile)

        pile = topic_module.load(name)
        words = topic_module.keywords(pile)
        marks = [i["look"] for i in pile["sources"]["images"] if i.get("look")]
        seen = {i.get("id") for i in pile["sources"]["images"]}
        fresh = list(pile["sources"]["images"])
        shot = [v for v in pile["sources"]["videos"] if v.get("file")]
        for index, video in enumerate(shot, start=1):
            say(index, len(shot), f"抽畫格 {video.get('outlet', '')}")
            moments = topic_module.frame_moments(video, words, most=4)
            if not moments:
                continue
            for made in topic_module.cut_frames(name, video, moments):
                if made["id"] in seen:
                    continue
                mark = stock_module.looks_like(ROOT / made["file"])
                if any(stock_module.alike(mark, other) for other in marks):
                    (ROOT / made["file"]).unlink(missing_ok=True)
                    continue
                marks.append(mark)
                seen.add(made["id"])
                fresh.append({**made, "look": mark})
        # 4. Stock and encyclopaedia pictures. The counts ask for five of each
        # kind because they cover different holes, and until now only frames
        # were ever collected by this button.
        marks = [i["look"] for i in fresh if i.get("look")]
        seen = {i.get("id") for i in fresh}
        here = ROOT / "assets" / "photos" / name
        here.mkdir(parents=True, exist_ok=True)
        per_term = topic_module.rules_module.at("collect.per_term", 3)
        for index, term in enumerate(shots[:10], start=1):
            say(index, len(shots[:10]), f"找圖 {term}")
            try:
                offered = stock_module.search_photos(term, count=per_term * 3)
            except Exception:                                     # noqa: BLE001
                continue
            got = 0
            for picture in offered:
                if got >= per_term or picture.id in seen:
                    continue
                target = here / f"{picture.id}.jpg"
                try:
                    stock_module.fetch(picture.url, target)
                except Exception:                                 # noqa: BLE001
                    continue
                mark = stock_module.looks_like(target)
                if any(stock_module.alike(mark, other) for other in marks):
                    target.unlink(missing_ok=True)
                    continue
                marks.append(mark)
                seen.add(picture.id)
                got += 1
                fresh.append({
                    "id": picture.id, "term": term, "kind": "stock",
                    "file": str(target.relative_to(ROOT)),
                    "caption": picture.about or term, "outlet": "Pexels",
                    "author": picture.author, "credit": "",
                    "answers": stock_module.answers(term, picture.about or ""),
                    "page": picture.page, "look": mark,
                    "size": [picture.width, picture.height]})
        # 5. The named things. A stock library has no photograph of Maduro or
        # of the village that flooded, because those are not concepts; an
        # encyclopaedia has already decided which picture is of this person.
        import time as time_module
        for index, title in enumerate(named[:10], start=1):
            if sum(1 for i in fresh if i.get("kind") == "real") >= \
                    topic_module.PICTURES["real"][1]:
                break
            say(index, len(named[:10]), f"查維基百科 {title}")
            try:
                offered = stock_module.wiki_lead(title)
            except Exception:                                     # noqa: BLE001
                continue
            for picture in offered:
                if picture.id in seen:
                    continue
                target = here / f"{picture.id}.jpg"
                try:
                    stock_module.fetch(picture.url, target)
                except Exception:                                 # noqa: BLE001
                    continue
                mark = stock_module.looks_like(target)
                if any(stock_module.alike(mark, other) for other in marks):
                    target.unlink(missing_ok=True)
                    continue
                marks.append(mark)
                seen.add(picture.id)
                fresh.append({
                    "id": picture.id, "term": title, "kind": "real",
                    "file": str(target.relative_to(ROOT)),
                    "caption": picture.about or title, "outlet": "維基百科",
                    "author": picture.author,
                    "credit": f"{picture.author}／{picture.licence or '見檔案頁'}",
                    "answers": stock_module.answers(title, picture.about or ""),
                    "page": picture.page, "look": mark,
                    "size": [picture.width, picture.height]})
            time_module.sleep(stock_module.WIKI_PAUSE)

        topic_module.replace_images(name, fresh)

        # 6. What people said underneath. Their words are closer to the
        # audience's than any press release, and they show where ordinary
        # people got stuck -- which is where the explaining is needed.
        pile = topic_module.load(name)
        shot = [v for v in pile["sources"]["videos"] if v.get("file")]
        # `say`, not `text`. read_comments renames it on the way in -- the same
        # word every line of a script uses for what is said -- and I wrote the
        # yt-dlp field name from memory, so every comment was read as empty and
        # skipped. Nothing failed: the job reported 完成 with none collected.
        added = 0
        asked = shot[:3]
        for index, video in enumerate(asked, start=1):
            say(index, len(asked), f"抓留言 {video.get('outlet', '')}")
            try:
                got = topic_module.read_comments(video["url"])
            except Exception:                                     # noqa: BLE001
                continue
            added += topic_module.add_voices(pile, video, got)
        topic_module.save(name, pile)
        # Asked and got nothing back. Said out loud because this is the
        # failure that looks most like success: three news channels with
        # comments turned off and a bug that reads every comment as empty
        # produce the same silence, and the job reported 完成 for both.
        if asked and not added:
            trouble.append(f"問了 {len(asked)} 支影片，一則留言都沒有")
            say(len(asked), len(asked),
                f"⚠ 問了 {len(asked)} 支影片，一則留言都沒有"
                "（可能是頻道關閉留言，也可能是抓取壞了）")

        pile = topic_module.load(name)
        pile["gathered"] = {"when": int(time_module.time()), "trouble": trouble}
        topic_module.save(name, pile)

    _job_run(f"收集：{name}", name, work)
    return {"started": name}


@app.post("/api/topic/judge")
def judge_sources(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Rule on what a model doubted: keep it, or drop it.

    Both directions are here because both were wrong once. Asked which of
    twenty-eight reports concerned the theft, a 7B model kept four and
    discarded six that plainly did -- including the article this topic began
    from -- while an airport malaria story it also discarded plainly did not.
    Whoever is looking settles it.
    """
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    urls = {str(one) for one in (payload.get("urls") or []) if one}
    keep = bool(payload.get("keep"))
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error

    kind = str(payload.get("kind") or "reports")
    rows = pile["sources"].get(kind) or []
    if keep:
        for one in rows:
            if one.get("url") in urls:
                one.pop("doubt", None)
    else:
        rows = [one for one in rows if one.get("url") not in urls]
        pile["sources"][kind] = rows
    topic_module.save(name, pile)
    return {"kept" if keep else "dropped": len(urls),
            "left": len(topic_module.doubted(pile, kind))}


@app.get("/api/topic/owns")
def topic_owns(name: str) -> dict[str, Any]:
    """What deleting this topic would take, before anybody presses anything.

    The dialog used to list three things from memory -- downloaded videos,
    photographs, cut frames -- and that list was written when a topic could
    not own a script. 「刪掉？」 without the actual contents is a question
    nobody can answer, which is the whole reason `confirmed()` takes a `lose`.
    """
    from core import script as script_module
    from core import topic as topic_module
    try:
        topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
    owned = topic_module.everything_for(name)
    return {
        "name": name,
        "scripts": script_module.for_topic(name),
        "films": [one for one in script_module.for_topic(name)
                  if (build_dir := ROOT / "assets" / "shorts" / f"{one}.mp4")
                  and build_dir.is_file()],
        "bytes": topic_module.weight(owned),
        "items": [{"path": str(one.relative_to(ROOT)),
                   "bytes": topic_module.weight([one])} for one in owned],
    }


@app.post("/api/topic/archive")
def archive_topic(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Put a topic out of the way, or bring it back.

    Not the same as deleting. A topic that came to nothing still records having
    asked -- which of the nineteen covered it, how far the balance got, why it
    stopped -- and that record is worth more than the row it occupies. Messina
    is the case in point: four outlets ran it and nobody on the right did, so it
    cannot become a short, and knowing that is worth keeping.
    """
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
    pile["archived"] = bool(payload.get("archived"))
    topic_module.save(name, pile)
    return {"name": name, "archived": pile["archived"]}


@app.post("/api/topic/note")
def set_note(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Why this topic is worth doing, and what to watch out for.

    The one thing here nothing can compute. Whether the opposition is easy to
    find is in `balance`; that a medical topic cannot be monetised with an AI
    voice is not anywhere, and it decides whether the film gets made at all.
    """
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
    pile["note"] = str(payload.get("note") or "").strip()
    topic_module.save(name, pile)
    return {"saved": True, "note": pile["note"]}


@app.post("/api/topic/audience")
def set_audience(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Who this topic is for.

    Stored rather than derived. It was being guessed from a table keyed on the
    topic's own words, which announced that a piece about studios being bought
    was for people whose jobs AI might take -- and the guess appeared in the
    same place an answer would, so nobody looked at it twice.
    """
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
    pile["audience"] = str(payload.get("audience") or "").strip()
    topic_module.save(name, pile)
    return {"saved": True, "audience": pile["audience"]}


@app.post("/api/topic/voices")
def gather_voices(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Fetch the comments under a topic's videos."""
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error

    heard = {item.get("url") for item in pile.get("voices") or []}
    added = 0
    for video in pile.get("sources", {}).get("videos") or []:
        url = video.get("url")
        if not url or url in heard:
            continue
        said = topic_module.read_comments(url)
        if not said:
            continue
        added += topic_module.add_voices(pile, video, said)
    topic_module.save(name, pile)
    return {"added": added, "total": topic_module.voice_count(pile)}


@app.post("/api/topic/footage")
def bring_footage(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Download a topic's videos so frames can be cut from them.

    The 25% of a short that is someone else's pictures comes from here -- both
    moving segments and single frames. A still costs nothing at the seam and
    can be held as long as the line needs, so most of the budget goes further
    as frames than as clips.
    """
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error

    most = int(payload.get("most") or 3)
    got = 0
    for video in (pile["sources"]["videos"] or [])[:most]:
        if video.get("file"):
            continue
        found = topic_module.bring_in(name, video)
        if found:
            video.update(found)
            got += 1
    topic_module.save(name, pile)
    have = sum(1 for v in pile["sources"]["videos"] if v.get("file"))
    return {"added": got, "have": have}


@app.post("/api/topic/frames")
def cut_frames(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Stills from the topic's own videos, spread across each one."""
    from core import stock as stock_module
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error

    per_video = int(payload.get("per_video") or 3)
    have = [item for item in pile["sources"]["images"] if item.get("look")]
    marks = [item["look"] for item in have]
    seen = {item.get("id") for item in pile["sources"]["images"]}
    # What this topic is about, in the language the captions are in.
    words = [w.strip() for w in (payload.get("words") or "").split(",") if w.strip()]
    words = words or topic_module.keywords(pile)
    added, skipped = 0, []
    for video in pile["sources"]["videos"]:
        if not video.get("file"):
            continue
        # Evenly spaced sampling is gone. It returned the titles, the anchor's
        # face and two people on stools, because a broadcast cuts every few
        # seconds and the shot that illustrates the story is not at 1/4, 2/4,
        # 3/4. The captions say when the story is being told; a video without
        # captions is not a frame source, and that is better than guessing.
        moments = topic_module.frame_moments(video, words, most=per_video)
        if not moments:
            skipped.append(video.get("outlet") or video.get("title", "")[:24])
            continue
        for made in topic_module.cut_frames(name, video, moments):
            if made["id"] in seen:
                continue
            look = stock_module.looks_like(ROOT / made["file"])
            if any(stock_module.alike(look, other) for other in marks):
                (ROOT / made["file"]).unlink(missing_ok=True)
                continue
            marks.append(look)
            seen.add(made["id"])
            pile["sources"]["images"].append({**made, "look": look})
            added += 1
    topic_module.save(name, pile)
    return {"added": added, "words": words, "skipped": skipped,
            "frames": sum(1 for i in pile["sources"]["images"]
                          if i.get("kind") == "frame")}


@app.post("/api/topic/find")
def find_pictures(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Look for one particular shot, right now, and show what came back.

    Batch collection answers "what might this topic need"; this answers "I need
    Trump wearing a hat for line twelve". Nothing is saved -- the point is to
    look first, because a specific request is usually specific about how it
    should look too.
    """
    from core import stock as stock_module
    want = str(payload.get("want") or "").strip()
    if not want:
        raise HTTPException(400, "要說你想找什麼（英文，圖庫不吃中文）")

    found = []
    for where, look in (("stock", stock_module.search_photos),
                        ("real", stock_module.search_commons)):
        try:
            for picture in look(want, count=6):
                found.append({
                    "kind": where, "id": picture.id, "url": picture.url,
                    "outlet": "Wikimedia Commons" if where == "real" else "Pexels",
                    "author": picture.author, "page": picture.page,
                    "caption": picture.about or want,
                    "credit": (f"{picture.author}／{picture.about.split('　')[-1]}"
                               if where == "real" else ""),
                    "size": [picture.width, picture.height]})
        except Exception:                                         # noqa: BLE001
            continue        # one library being unavailable is not a failure
    return {"want": want, "found": found}


@app.post("/api/topic/keep")
def keep_picture(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Take one of the pictures that came back."""
    from core import stock as stock_module
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    picture = payload.get("picture") or {}
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
    if not picture.get("url"):
        raise HTTPException(400, "沒有指定哪一張")

    here = ROOT / "assets" / "photos" / name
    target = here / f"{picture.get('id', 'kept')}.jpg"
    try:
        stock_module.fetch(picture["url"], target)
    except Exception as error:                                    # noqa: BLE001
        raise HTTPException(502, f"抓不下來：{error}") from error

    look = stock_module.looks_like(target)
    pile["sources"]["images"].append({
        "id": str(picture.get("id")), "kind": picture.get("kind", "stock"),
        "term": str(payload.get("want") or picture.get("caption", ""))[:60],
        "file": str(target.relative_to(ROOT)),
        "caption": picture.get("caption", ""), "outlet": picture.get("outlet", ""),
        "author": picture.get("author", ""), "credit": picture.get("credit", ""),
        "page": picture.get("page", ""), "look": look,
        "size": picture.get("size") or [0, 0]})
    topic_module.save(name, pile)
    return {"kept": picture.get("id"),
            "pictures": topic_module.picture_mix(pile)}


@app.post("/api/topic/images")
def gather_images(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Photographs for a topic, kept as a pile to choose from later."""
    from core import stock as stock_module
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error

    terms = payload.get("terms") or []
    if not terms:
        raise HTTPException(400, "要給搜尋詞（英文，圖庫不吃中文）")
    # Where to ask. Stock stands in for the abstract -- a bill, a queue, a
    # meter; Commons has the actual person, street and building, which no
    # stock library holds because those are not concepts.
    # Commons is asked by name, not by concept. Ask it for a concept and it
    # wanders -- "server rack" found a bicycle rack -- but an encyclopaedia has
    # already decided which picture is of this person, so for anything with an
    # article the lead image is taken instead of searching at all.
    where = str(payload.get("where") or "stock")
    search = {"commons": stock_module.search_commons,
              "wiki": stock_module.wiki_lead}.get(where, stock_module.search_photos)

    # Six pictures from one search are six pictures of the same thing. Variety
    # comes from asking more questions, not from taking more of one answer, so
    # the cap is per term and near-identical results are dropped on arrival.
    PER_TERM = 3
    failed: list[str] = []
    here = ROOT / "assets" / "photos" / name
    seen = {item.get("id") for item in pile["sources"]["images"]}
    marks = [item["look"] for item in pile["sources"]["images"] if item.get("look")]
    added = 0
    for term in terms[:10]:
        try:
            found = (search(term) if where == "wiki"
                     else search(term, count=PER_TERM * 3))
        except Exception:                                         # noqa: BLE001
            # One term failing is one term's worth of pictures, not the batch.
            # Asking a library five times quickly is enough to be refused once.
            failed.append(term)
            continue
        kept_here = 0
        for picture in found:
            if kept_here >= PER_TERM:
                break
            if picture.id in seen:
                continue
            target = here / f"{picture.id}.jpg"
            try:
                stock_module.fetch(picture.url, target)
            except Exception:                                     # noqa: BLE001
                continue
            mark = stock_module.looks_like(target)
            if any(stock_module.alike(mark, other) for other in marks):
                target.unlink(missing_ok=True)        # already have one like it
                continue
            marks.append(mark)
            kept_here += 1
            pile["sources"]["images"].append({
                "id": picture.id, "term": term,
                "file": str(target.relative_to(ROOT)),
                "caption": picture.about or term,
                "kind": "stock" if where == "stock" else "real",
                "outlet": ("Pexels" if where == "stock"
                           else "維基百科" if where == "wiki"
                           else "Wikimedia Commons"),
                "author": picture.author,
                # Commons is mostly CC BY or CC BY-SA: the author and licence
                # have to reach the screen, so they travel with the picture
                # rather than being looked up again later.
                "credit": ("" if where == "stock" else
                           f"{picture.author}／{picture.licence or '見檔案頁'}"),
                # How much of what we asked for the picture's own caption
                # actually says. Recorded now, while both halves are in hand.
                "answers": stock_module.answers(term, picture.about or ""),
                "page": picture.page, "look": mark,
                "size": [picture.width, picture.height]})
            seen.add(picture.id)
            added += 1
    topic_module.save(name, pile)
    return {"added": added, "total": len(pile["sources"]["images"]),
            "failed": failed}


@app.get("/media/pic/{picture:path}")
def script_picture(picture: str) -> FileResponse:
    """A picture -- or a piece of footage -- that a script names.

    Matched against what some topic gathered, so the path is checked rather
    than trusted. Footage is served whole and the page seeks within it, which
    is how a chosen passage can be watched on the script page instead of being
    represented by one frozen frame.
    """
    from core import topic as topic_module
    for name in topic_module.names():
        pile = topic_module.load(name)
        if picture in {item.get("file") for item in pile["sources"]["images"]}:
            return FileResponse(ROOT / picture, media_type="image/jpeg")
        if picture in {item.get("file") for item in pile["sources"]["videos"]}:
            return FileResponse(ROOT / picture, media_type="video/mp4")
    raise HTTPException(404, "找不到這張圖")


@app.get("/media/card/{script}/{card}")
def script_card(script: str, card: str) -> FileResponse:
    """One drawn shot. Named after a hash of its own specification, so the
    path is checked by looking rather than trusted."""
    from core import cards as cards_module
    target = cards_module.CARD_DIR / script / card
    if not target.is_file() or target.suffix != ".png":
        raise HTTPException(404, "找不到這張卡")
    return FileResponse(target, media_type="image/png")


@app.get("/media/photo/{name}/{picture}")
def topic_photo(name: str, picture: str) -> FileResponse:
    """One gathered photograph, matched against the topic's own list."""
    from core import topic as topic_module
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
    wanted = f"assets/photos/{name}/{picture}"
    if wanted not in {item.get("file") for item in pile["sources"]["images"]}:
        raise HTTPException(404, "找不到這張照片")
    return FileResponse(ROOT / wanted, media_type="image/jpeg")


@app.post("/api/topic/script")
def make_script(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Write a script from what has been gathered.

    Refused while the pile is one-sided: a script written from sources that all
    agree is a pamphlet, and no amount of drafting fixes that afterwards.
    """
    from core import topic as topic_module
    name = str(payload.get("name") or "")
    try:
        pile = topic_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error

    enough, why = topic_module.ready(pile)
    if not enough and not payload.get("anyway"):
        raise HTTPException(400, f"素材還不夠：{why}")

    # Which shape to write. Chosen here rather than corrected afterwards: it
    # decides the roles, the pacing, the borrowed ceiling and which prompt is
    # sent, so choosing it later means relabelling every line.
    from core import brief as brief_module
    from core import rules as rules_module
    house = str(payload.get("format") or rules_module.FALLBACK)
    if house not in rules_module.formats():
        raise HTTPException(400, f"沒有這個公版：{house}")

    reachable, complaint = _model_reachable()
    if not reachable:
        raise HTTPException(503, f"{complaint}　寫文案需要它。")
    # The prompt is assembled either way, so a missing name is found here
    # rather than at the moment a model is connected.
    brief_module.prompt(name, house)

    from core import writer as writer_module
    into = str(payload.get("as") or "").strip() or None

    def work(say) -> None:
        made = writer_module.write(name, house, into, say)
        say(3, 3, f"{made['name']}　{made['lines']} 句　{made['seconds']}s　"
                  + ("全部通過" if not made["faults"]
                     else "不合格：" + "、".join(made["faults"])))

    _job_run(f"寫文案：{name}（{house}）", name, work)
    return {"started": name, "format": house}


# ------------------------------------------------------------------ scripts

@app.get("/api/scripts")
def get_scripts() -> dict[str, Any]:
    from core import script as script_module
    return {"scripts": script_module.listing(), "limit": script_module.LIMIT,
            "per_second": script_module.PER_SECOND}


@app.get("/api/script")
def get_script(name: str) -> dict[str, Any]:
    """One script with its arithmetic already done: a length nobody has to
    guess at, and every line that states something without saying where it
    came from."""
    from core import script as script_module
    try:
        found = script_module.load(name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error
    measured = script_module.measure(found)

    # The picture's own caption, brought over from the topic and hung beside
    # the label the writer typed. A line reading 示意：帳單特寫 next to a
    # caption reading `fuse box on a white wall` gives itself away; with only
    # the label on screen it never did.
    from core import topic as topic_module
    known: dict[str, dict[str, Any]] = {}
    try:
        pile = topic_module.load(found.get("topic", ""))
        known = {item["file"]: item for item in pile["sources"]["images"]
                 if item.get("file")}
    except (ValueError, FileNotFoundError):
        pass
    footage = {v["file"]: v for v in (pile["sources"]["videos"] if known else [])
               if v.get("file")}
    for line in measured["lines"]:
        source = known.get(line.get("pic") or "")
        if source:
            line["caption"] = source.get("caption", "")
            line["credit"] = source.get("credit", "")
            line["said"] = source.get("said", "")
            line["outlet"] = source.get("outlet", "")
        if line.get("clip"):
            shot = footage.get(line["clip"]["file"], {})
            line["outlet"] = shot.get("outlet", "")
            line["credit"] = f"畫面來源：{shot.get('outlet', '')}"
    # Cards are drawn on the way out, so the page shows the actual shot rather
    # than the sentence describing it. Rendering is cached on the spec's own
    # hash: an edited card gets a new file, an unchanged one is not redrawn.
    from core import cards as cards_module
    drawn: set[str] = set()
    for line in measured["lines"]:
        if line.get("card"):
            try:
                line["card_file"] = cards_module.render(name, line["card"])
                drawn.add(Path(line["card_file"]).name)
            except Exception as error:                            # noqa: BLE001
                line["card_error"] = str(error)
    cards_module.sweep(name, drawn)
    return {**found, "measured": measured}


@app.post("/api/script/line")
def edit_line(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Change one line's words by hand.

    The measurements are advice, not a gate: a line over the suggested width
    is saved and reported, because a writer looking at the frame can see that
    a fourteenth character fits where the arithmetic says it does not, and a
    tool that refuses the edit just moves the work into a text editor where
    nothing is measured at all.
    """
    from core import script as script_module
    name = str(payload.get("name") or "")
    index = int(payload.get("line") or 0) - 1
    try:
        found = script_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
    lines = found.get("lines") or []
    if not 0 <= index < len(lines):
        raise HTTPException(400, "沒有這一句")

    role = payload.get("role")
    if role is not None:
        # Which of 起承轉合 a line is, changed by hand. The label is a claim
        # about the writing, and the writer is the worst judge of it -- the
        # Nepal turn was on a line that only remarks on the argument, and every
        # check passed because they all measure the label rather than the
        # thing. Somebody reading it has to be able to move it.
        from core import script as script_module
        roles = script_module.roles_of(found)
        if role and role not in roles:
            raise HTTPException(400, "角色只能是 " + "／".join(roles))
        lines[index]["role"] = role
        script_module.save(name, found)
        return {"saved": True, "line": index + 1, "role": role,
                "measured": script_module.measure(found)}

    say = str(payload.get("say") or "").strip()
    if not say:
        raise HTTPException(400, "台詞不能是空的")
    lines[index]["say"] = say
    # The duration was computed from the old words; recompute it unless this
    # line was given one on purpose (a clip's length, a held picture).
    if not lines[index].get("clip"):
        lines[index]["seconds"] = round(
            max(1.9, script_module.spoken_length(say) / 4.6), 2)
    script_module.save(name, found)
    return {"saved": True, "line": index + 1,
            "seconds": lines[index]["seconds"],
            "rows": script_module.wrap(say),
            "measured": script_module.measure(found)}


@app.get("/api/formats")
def get_formats() -> dict[str, Any]:
    """The house styles a script can be written in, with why each exists."""
    from core import rules as rules_module
    known = rules_module.formats()
    return {"formats": [{"key": key, **spec} for key, spec in known.items()]}


@app.post("/api/format")
def save_format(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Create or change a house style.

    The thresholds a format does not name fall through to rules.json, so a new
    shape says only what makes it different -- and the two cannot drift apart,
    which is the fault this whole file arrangement exists to avoid.
    """
    from core import rules as rules_module
    key = str(payload.get("key") or "").strip()
    spec = payload.get("spec")
    if not isinstance(spec, dict):
        raise HTTPException(400, "沒有內容")
    try:
        rules_module.save_house(key, spec)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    return {"saved": key, "formats": [{"key": k, **one}
                                      for k, one in rules_module.formats().items()]}


@app.delete("/api/format")
def delete_format(key: str) -> dict[str, Any]:
    """Remove a house style, unless scripts are written in it.

    Refused rather than cascaded: deleting the shape a script was written in
    turns every one of its roles into a word the checks do not recognise, and
    nothing here can guess what they should become instead.
    """
    from core import rules as rules_module
    using = rules_module.used_by(key)
    if using:
        raise HTTPException(400, f"還有 {len(using)} 份文案在用："
                                 + "、".join(using))
    try:
        gone = rules_module.drop_house(key)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not gone:
        raise HTTPException(404, f"沒有這個公版：{key}")
    return {"deleted": key}


@app.post("/api/script/format")
def set_format(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Change which house style a script is written in.

    Changing it re-checks everything against different thresholds, and the
    roles usually stop being valid words -- an argument's 起承轉合 means
    nothing to a story. That is reported rather than repaired: which line is
    the scene and which is a doubt is a reading of the script, and guessing it
    would be inventing the answer to the one question the format exists to ask.
    """
    from core import rules as rules_module
    from core import script as script_module
    name = str(payload.get("name") or "")
    house = str(payload.get("format") or "")
    if house not in rules_module.formats():
        raise HTTPException(400, f"沒有這個公版：{house}")
    try:
        found = script_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
    found["format"] = house
    script_module.save(name, found)
    return {"saved": True, "measured": script_module.measure(found)}


@app.post("/api/script/about")
def set_about(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Change a script's own description of itself: the view, the tone, and
    who it is for.

    Who it is for is the field worth being able to edit. It is derived from a
    table of topics when a script is written -- 電費 to whoever pays bills, 股市
    to whoever holds shares -- and the table cannot know that a piece about big
    tech buying studios is for people who pay for streaming rather than for
    people whose jobs AI might take. It got that one wrong, and it is the field
    the whole ending is written towards.
    """
    from core import script as script_module
    name = str(payload.get("name") or "")
    try:
        found = script_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
    for field in ("for", "view", "tone"):
        if field in payload:
            found[field] = str(payload[field] or "").strip()
    script_module.save(name, found)
    return {"saved": True, "for": found.get("for", ""),
            "view": found.get("view", ""), "tone": found.get("tone", "")}


@app.post("/api/script/build")
def build_script(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Render a script into the finished film.

    Every gate runs before a frame is encoded, which is worth doing when a
    build costs four minutes. The contact sheet is made at the end and is not
    optional: looking at what was actually rendered is the one check nothing
    else can do.
    """
    from core import build as build_module
    from core import script as script_module
    name = str(payload.get("name") or "")
    try:
        script_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error

    def work(say) -> None:
        made = build_module.build(
            name, say=lambda index, total, line: say(index, total, line))
        say(made["shots"], made["shots"], f"{made['seconds']}s　{made['file']}")

    _job_run(f"壓片：{name}", name, work)
    return {"started": name}


@app.get("/api/script/film")
def get_film(name: str) -> dict[str, Any]:
    """Where the finished film and its contact sheet are, if they exist."""
    from core import build as build_module
    film = build_module.OUT_DIR / f"{name}.mp4"
    sheet = build_module.OUT_DIR / f"{name}.contact.jpg"
    return {"film": str(film.relative_to(ROOT)) if film.is_file() else "",
            "sheet": str(sheet.relative_to(ROOT)) if sheet.is_file() else "",
            "size": film.stat().st_size if film.is_file() else 0,
            "made": int(film.stat().st_mtime) if film.is_file() else 0}


@app.get("/media/film/{name}")
def serve_film(name: str) -> FileResponse:
    from core import build as build_module
    target = build_module.OUT_DIR / f"{name}.mp4"
    if not target.is_file():
        raise HTTPException(404, "還沒壓")
    return FileResponse(target, media_type="video/mp4")


@app.get("/media/sheet/{name}")
def serve_sheet(name: str) -> FileResponse:
    from core import build as build_module
    target = build_module.OUT_DIR / f"{name}.contact.jpg"
    if not target.is_file():
        raise HTTPException(404, "還沒壓")
    return FileResponse(target, media_type="image/jpeg")


@app.post("/api/script/seen")
def mark_seen(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Record that somebody looked at a line's picture.

    The gate has existed for a while and nothing could fill it but me editing
    JSON. Which is the wrong shape twice over: it needs no model, and I am the
    one who demonstrably forgets -- the first pictures in this project were
    chosen without being opened, against a rule I had written myself.
    """
    from core import script as script_module
    name = str(payload.get("name") or "")
    index = int(payload.get("line") or 0) - 1
    try:
        found = script_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error
    lines = found.get("lines") or []
    if not 0 <= index < len(lines):
        raise HTTPException(400, "沒有這一句")
    lines[index]["seen"] = bool(payload.get("seen"))
    script_module.save(name, found)
    return {"saved": True, "line": index + 1, "seen": lines[index]["seen"],
            "unchecked": len(script_module.unchecked(found))}


@app.post("/api/script/lines")
def edit_lines(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Save a batch of hand edits, all of them or none.

    One at a time was wrong in two ways. Every keystroke reaching disk meant
    there was no such thing as an unsaved change, so leaving the page could not
    discard anything and a half-finished thought was already the script. And a
    role moved on its own can pass while leaving the sequence broken -- 合
    before 轉 is not a shape anybody is working towards, and it is only
    visible when the whole set is considered together.

    So the new lines are assembled, checked, and only then written. A rejected
    batch leaves the script exactly as it was, which is the same order that
    keeps a failed re-collection from destroying the pictures it was replacing.
    """
    from core import script as script_module
    name = str(payload.get("name") or "")
    changes = payload.get("changes") or []
    try:
        found = script_module.load(name)
    except (ValueError, FileNotFoundError) as error:
        raise HTTPException(404, str(error)) from error

    lines = [dict(line) for line in (found.get("lines") or [])]
    for change in changes:
        index = int(change.get("line") or 0) - 1
        if not 0 <= index < len(lines):
            raise HTTPException(400, f"沒有第 {index + 1} 句")
        if "role" in change:
            role = str(change["role"] or "")
            roles = script_module.roles_of(found)
            if role and role not in roles:
                raise HTTPException(400, "角色只能是 " + "／".join(roles))
            lines[index]["role"] = role
        if "say" in change:
            say = str(change["say"] or "").strip()
            if not say:
                raise HTTPException(400, f"第 {index + 1} 句的台詞不能是空的")
            lines[index]["say"] = say
            if not lines[index].get("clip"):
                lines[index]["seconds"] = round(
                    max(script_module.LEAST_SECONDS,
                        script_module.spoken_length(say) /
                        script_module.READ_PER_SECOND), 2)

    broken = script_module.out_of_order(
        [line.get("role", "") for line in lines],
        script_module.roles_of(found))
    if broken:
        raise HTTPException(400, f"起承轉合的順序會壞掉：{broken}")

    found["lines"] = lines
    script_module.save(name, found)
    return {"saved": len(changes), "measured": script_module.measure(found)}


@app.delete("/api/script")
def drop_script(name: str) -> dict[str, Any]:
    """Remove a script, and the film and cards made from it.

    Into trash/ rather than deleted: a script is the only place a set of
    judgements lives -- which picture for which line, which passage, which
    card -- and every one of them took looking at something. The film and the
    drawn cards do go, because both rebuild exactly from the script.
    """
    import shutil
    import time as time_module
    from core import build as build_module
    from core import cards as cards_module
    from core import script as script_module
    try:
        path = script_module.path_for(name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not path.is_file():
        raise HTTPException(404, f"找不到文案 {name}")

    bin_here = ROOT / "trash"
    bin_here.mkdir(exist_ok=True)
    shutil.move(str(path), bin_here / f"{name}.{int(time_module.time())}.json")

    gone = []
    for target in (build_module.OUT_DIR / f"{name}.mp4",
                   build_module.OUT_DIR / f"{name}.contact.jpg"):
        if target.is_file():
            target.unlink()
            gone.append(target.name)
    for folder in (build_module.OUT_DIR / f".{name}", cards_module.CARD_DIR / name):
        if folder.is_dir():
            shutil.rmtree(folder)
            gone.append(folder.name)
    return {"deleted": name, "moved_to": "trash/", "also_removed": gone}


@app.get("/scripts")
def scripts_page() -> HTMLResponse:
    return HTMLResponse((Path(__file__).parent / "static" / "scripts.html")
                        .read_text(encoding="utf-8"))


@app.get("/api/layouts")
def get_layouts() -> dict[str, Any]:
    """The house styles a run can start from."""
    from core import layouts as layouts_module
    return {"layouts": layouts_module.listing()}


@app.get("/api/layout")
def get_layout(name: str) -> dict[str, Any]:
    """One layout in full, so it can be drawn rather than only named."""
    from core import layouts as layouts_module
    try:
        return layouts_module.load(name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(404, str(error)) from error


@app.post("/api/layout")
def save_layout(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Keep the frame as it stands now as a house style.

    What is saved is the arrangement without the timing: the picture's box, the
    caption style, the channel mark. The cards that happen to be on screen
    belong to this video and are left behind, which is the whole reason a
    layout is a different thing from a scene.
    """
    from core import layouts as layouts_module
    name = str(payload.get("name") or "").strip()
    scene = payload.get("scene")
    if not isinstance(scene, dict) or not isinstance(scene.get("elements"), list):
        raise HTTPException(400, "沒有版面可以存")
    try:
        path = layouts_module.save(name, scene, str(payload.get("note") or ""))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    kept = layouts_module.load(name)
    dropped = len(scene["elements"]) - len(kept["elements"])
    return {"saved": name, "at": str(path.relative_to(ROOT)),
            "kept": len(kept["elements"]), "dropped": dropped,
            "layouts": layouts_module.listing()}


@app.delete("/api/layout")
def remove_layout(name: str) -> dict[str, Any]:
    from core import layouts as layouts_module
    try:
        path = layouts_module.path_for(name)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not path.is_file():
        raise HTTPException(404, f"找不到版面 {name}")
    _to_trash(path)
    return {"removed": name, "layouts": layouts_module.listing()}


@app.get("/api/sites")
def sites(q: str = "") -> dict[str, Any]:
    """Which sites work. A list of 1700 names is not an answer, so it is
    searchable: type where the video is and see whether it is in there."""
    everything = _sites()
    wanted = q.strip().lower()
    found = [name for name in everything if wanted in name.lower()] if wanted else []
    return {"total": len(everything), "query": q,
            "matches": found[:40], "more": max(0, len(found) - 40)}


def _model_reachable() -> tuple[bool, str]:
    """Whether the model that does the translating can be reached right now."""
    from core import llm as llm_module
    from core import settings as settings_module
    try:
        provider, options = settings_module.llm_options(argparse.Namespace())
        client = llm_module.build(provider, options)
        if client is None:                  # provider "none": nothing to reach
            return True, ""
        client.ensure_ready()
        return True, ""
    except Exception as error:                                    # noqa: BLE001
        return False, f"連不上 {provider}：{error}"


def _produce_worker(source: str, name: str, extra: list[str]) -> None:
    """Run the pipeline and report which of its eight steps it is on.

    produce.py already announces every step on stdout, so progress is read from
    what it says rather than guessed at from a timer. Reading line by line also
    means the log survives a failure: when it stops, the last thing it printed
    is on screen.
    """
    command = [str(ROOT / ".venv/bin/python"), "-u", str(ROOT / "produce.py"),
               source, "--project", name, *extra]
    began = time.time()
    with _produce_lock:
        _produce.update(state="running", step=0, what="開始", message="",
                        warning="", project=name, started=began, log=[])
    try:
        handle = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        _produce_process["handle"] = handle
        for line in handle.stdout:
            line = line.rstrip()
            if not line:
                continue
            with _produce_lock:
                _produce["log"] = (_produce["log"] + [line])[-200:]
                found = STEP_LINE.match(line)
                if found:
                    _produce["step"] = int(found.group(1))
                    _produce["steps"] = int(found.group(2))
                    _produce["what"] = found.group(3)
        code = handle.wait()
        with _produce_lock:
            if code == 0:
                # produce.py says so when it has to carry on without the model.
                # A run that finished without translating is not a success, and
                # calling it one is how you end up publishing English subtitles.
                gave_up = [line for line in _produce["log"]
                           if "無法連線" in line or "Skipping proofreading" in line]
                _produce.update(state="done", step=_produce["steps"],
                                warning="；".join(line.strip() for line in gave_up),
                                message=f"完成：{name}")
            elif _produce_process["stopping"]:
                # Asked to stop. A non-zero exit is what stopping looks like,
                # not a failure, and reporting it as one is alarming for no
                # reason.
                _produce.update(state="idle", step=0, message="已停止")
            else:
                last = next((line for line in reversed(_produce["log"])
                             if line.strip()), "")
                _produce.update(state="error",
                                message=f"第 {_produce['step']} 步失敗　{last[:200]}")
    except Exception as error:                                    # noqa: BLE001
        traceback.print_exc()
        with _produce_lock:
            _produce.update(state="error", message=str(error))
    finally:
        _produce_process["handle"] = None
        _produce_process["stopping"] = False


@app.post("/api/produce")
def start_produce(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    source = str(payload.get("source") or "").strip()
    if not source:
        raise HTTPException(400, "要有一個網址或一個檔案路徑")
    if not source.startswith(("http://", "https://")):
        # A local file is allowed, but it has to be a file that exists: the
        # alternative is finding out eight minutes later.
        if not Path(source).expanduser().is_file():
            raise HTTPException(400, f"找不到檔案 {source}")
    name = str(payload.get("name") or "").strip()
    output = project_dir(name)                  # also checks the name is a name
    if output.exists():
        raise HTTPException(400, f"{name} 已經存在了，換個名字")

    # Ask the model whether it is there before spending twelve minutes finding
    # out. Without this the pipeline degrades quietly: it transcribes, cannot
    # reach anyone to translate, and finishes "successfully" with English
    # subtitles -- the worst outcome, because it looks like it worked.
    reachable, why = _model_reachable()
    if not reachable:
        raise HTTPException(503, f"{why}　字幕翻譯、校正和圖卡都需要它，"
                                 "所以現在跑只會得到一支英文字幕的影片。")

    extra = ["--render"] if payload.get("render", True) else []
    layout = str(payload.get("layout") or "").strip()
    if layout:
        from core import layouts as layouts_module
        if layout not in layouts_module.names():
            raise HTTPException(404, f"找不到版面 {layout}")
        extra += ["--layout", layout]
    with _produce_lock:
        if _produce["state"] == "running":
            return dict(_produce)
    threading.Thread(target=_produce_worker, args=(source, name, extra),
                     daemon=True).start()
    time.sleep(0.2)
    with _produce_lock:
        return dict(_produce)


@app.get("/api/produce")
def produce_status() -> dict[str, Any]:
    with _produce_lock:
        state = dict(_produce)
    state["seconds"] = round(time.time() - state["started"], 1) if state["started"] else 0.0
    state["log"] = state["log"][-12:]
    return state


@app.post("/api/produce/stop")
def stop_produce() -> dict[str, Any]:
    """Stop a run in progress. Killing the parent alone leaves its ffmpeg
    child writing to the same file, so the whole process group goes."""
    handle = _produce_process.get("handle")
    if handle is None:
        raise HTTPException(400, "現在沒有在跑")
    _produce_process["stopping"] = True
    handle.terminate()
    try:
        handle.wait(timeout=10)
    except subprocess.TimeoutExpired:
        handle.kill()
    # The worker sets the final state when it notices the process is gone;
    # this only reports what was asked for.
    with _produce_lock:
        return dict(_produce)


@app.get("/produce")
def produce_page() -> HTMLResponse:
    return HTMLResponse((Path(__file__).parent / "static" / "produce.html")
                        .read_text(encoding="utf-8"))


@app.get("/api/videos")
def videos() -> dict[str, Any]:
    """Footage on disk that could become a project: downloads and clips.

    Anything already used by a run is still offered -- the same interview can
    reasonably be worked on twice, and refusing would be guessing at why.
    """
    found = []
    for directory in (WORK, ROOT / CLIP_DIR):
        if not directory.is_dir():
            continue
        for video in sorted(directory.glob("*.mp4")):
            beside = video.with_suffix(".srt")
            found.append({
                "path": str(video.relative_to(ROOT)),
                "name": video.stem[:48],
                "where": directory.name,
                "seconds": round(media.duration(video), 2),
                # A subtitle file already sitting next to it saves transcribing
                # something that has been transcribed.
                "srt": str(beside.relative_to(ROOT)) if beside.is_file() else None,
            })
    return {"videos": found}


@app.post("/api/project/new")
def new_project(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Make the smallest thing the editor will open: where the video is, and a
    subtitle file. Empty subtitles mean no subtitles, which is a fair
    description of a video nobody has listened to yet."""
    name = str(payload.get("name") or "").strip()
    output = project_dir(name)
    if output.exists():
        raise HTTPException(400, f"{name} 已經存在了，換個名字")

    source = str(payload.get("source") or "")
    offered = {item["path"]: item for item in videos()["videos"]}
    if source not in offered:
        raise HTTPException(404, f"找不到影片 {source}")

    output.mkdir(parents=True)
    (output / "run.json").write_text(
        json.dumps({"source": str((ROOT / source).resolve()), "made_here": True,
                    "recovered_segments": 0}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    # A subtitle file beside the video is worth taking; otherwise an empty one,
    # which is what marks the directory as a project.
    beside = offered[source]["srt"]
    text = (ROOT / beside).read_text(encoding="utf-8") if beside else ""
    for kind in ("zh", "bilingual"):
        (output / f"subtitles_{kind}.srt").write_text(text, encoding="utf-8")
    return {"made": name, "lines": text.count("-->"),
            "projects": list_projects()}


@app.post("/api/project/remove")
def remove_project(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Take a whole run out of the list: video, subtitles, layout and all."""
    name = str(payload.get("name") or "")
    if name not in {item["name"] for item in list_projects()}:
        raise HTTPException(404, f"找不到專案 {name}")
    if name == config["paths"]["output"].name:
        raise HTTPException(400, f"{name} 正開著，先切到別的專案再刪")
    landed = _to_trash(project_dir(name))
    return {"removed": name, "at": str(landed.relative_to(ROOT)),
            "projects": list_projects()}


def _allowed_source(path: str) -> Path:
    """A source the assembly page offered, resolved. Checked against that list
    rather than joined, so the page cannot be talked into reading elsewhere."""
    if path not in {item["source"] for item in sources()["sources"]}:
        raise HTTPException(404, f"找不到片源 {path}")
    return ROOT / path


@app.get("/media/sourcestrip")
def source_strip(path: str) -> FileResponse:
    """Thumbnails across a whole source, for trimming a piece by eye."""
    source = _allowed_source(path)
    strip = FILMSTRIP / f"src_{cache_key(source)}.png"
    seconds = media.duration(source)
    media.ensure_filmstrip(_playable(source), strip, seconds,
                           every=max(1.0, seconds / 60))
    return FileResponse(strip, media_type="image/png")


@app.get("/media/sourceposter")
def source_poster(path: str) -> FileResponse:
    """One frame, so a片源 can be recognised without reading its name.

    Taken a little way in rather than at zero: the first frame of a video is
    often black, a slate, or a fade, and a black rectangle identifies nothing.
    """
    source = _allowed_source(path)
    poster = FILMSTRIP / f"poster_{cache_key(source)}.jpg"
    if not poster.is_file():
        poster.parent.mkdir(parents=True, exist_ok=True)
        seconds = media.duration(source)
        subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{min(max(1.0, seconds * 0.1), max(0.0, seconds - 0.5)):.2f}",
            "-i", str(_playable(source)), "-frames:v", "1",
            "-vf", "scale=192:108:force_original_aspect_ratio=increase,crop=192:108",
            "-q:v", "4", str(poster),
        ], check=True)
    return FileResponse(poster, media_type="image/jpeg")


def _playable(source: Path) -> Path:
    """A copy a browser can decode. The originals are often AV1, which most
    cannot, and the proxy exists for exactly this reason."""
    if source.suffix.lower() == ".mp4" and source.parent == ROOT / CLIP_DIR:
        return source
    return media.ensure_proxy(source, PROXY / f"{cache_key(source)}.mp4")


@app.get("/media/sourceplay")
def source_play(path: str) -> FileResponse:
    source = _allowed_source(path)
    return FileResponse(_playable(source), media_type="video/mp4",
                        headers={"Cache-Control": "no-store"})


def _variant(srt: Path, kind: str) -> Path:
    """The same subtitles in another pairing. A run writes subtitles_zh.srt and
    subtitles_bilingual.srt side by side; a source with only the one it named
    keeps it."""
    sibling = srt.with_name(f"subtitles_{kind}.srt")
    return sibling if sibling.is_file() else srt


def _assemble_worker(pieces: list[dict[str, Any]], name: str) -> None:
    from core import assemble as assemble_module
    try:
        output = project_dir(name)
        output.mkdir(parents=True, exist_ok=True)
        video = output / "assembled.mp4"
        laid = assemble_module.assemble(
            [{**piece, "source": ROOT / piece["source"],
              "srt": (ROOT / piece["srt"]) if piece.get("srt") else None}
             for piece in pieces],
            video, progress=PREVIEWS / f"{name}.progress",
        )
        # A layout is timed against the video it was made for, so it moves with
        # the pieces exactly as the captions do.
        # A run's video lives in work/, so the run directory is found through
        # its subtitles rather than through the video path. A clip has neither.
        laid_out = assemble_module.merge_scene(
            [{**piece,
              "scene": (ROOT / piece["srt"]).parent / "scene.json"
                       if piece.get("srt") else None}
             for piece in pieces])
        if laid_out:
            (output / "scene.json").write_text(
                json.dumps(laid_out, ensure_ascii=False, indent=2), encoding="utf-8")

        # A run keeps its subtitles in two pairings, and the editor expects both
        # here too. They are merged separately: writing one file's cues under
        # both names left the Chinese-only track holding bilingual captions.
        for kind in ("zh", "bilingual"):
            cues = assemble_module.merge_cues(
                [{**piece, "srt": _variant(ROOT / piece["srt"], kind)
                           if piece.get("srt") else None}
                 for piece in pieces])
            assemble_module.write_srt(cues, output / f"subtitles_{kind}.srt")
        # The pieces are kept so the assembly can be opened and adjusted rather
        # than rebuilt from memory.
        (output / "assembly.json").write_text(
            json.dumps({"pieces": laid["pieces"]}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        (output / "run.json").write_text(
            json.dumps({"source": str(video), "assembled": True,
                        "recovered_segments": 0}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        with _assembly_lock:
            _assembly.update(state="done", output=name,
                             message=f"完成：{name}（{laid['duration']:.1f} 秒，"
                                     f"{len(cues)} 句字幕）")
    except Exception as error:                                    # noqa: BLE001
        traceback.print_exc()
        with _assembly_lock:
            _assembly.update(state="error", message=str(error), output=None)


@app.post("/api/assemble")
def start_assembly(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    target_dir = project_dir(name)          # also checks the name is a name
    pieces = payload.get("pieces") or []
    if not pieces:
        raise HTTPException(400, "至少要有一段")
    # An assembly can be a片源 of the next one, so the name offered is often the
    # name of something in the list. Writing over a file that is being read from
    # would leave neither: refuse while both still exist.
    target = (target_dir / "assembled.mp4").resolve()
    if any((ROOT / piece.get("source", "")).resolve() == target for piece in pieces):
        raise HTTPException(400, f"{name} 自己就在片源裡，不能存回同一個名字。"
                                 "換個名字，舊的那支才不會被蓋掉")
    with _assembly_lock:
        if _assembly["state"] == "running":
            return dict(_assembly)
        _assembly.update(state="running", message=f"組裝 {len(pieces)} 段…", output=None)
    threading.Thread(target=_assemble_worker, args=(pieces, name), daemon=True).start()
    return dict(_assembly)


@app.get("/api/assemble")
def assembly_status() -> dict[str, Any]:
    with _assembly_lock:
        return dict(_assembly)


@app.get("/assemble")
def assemble_page() -> HTMLResponse:
    return HTMLResponse((Path(__file__).parent / "static" / "assemble.html")
                        .read_text(encoding="utf-8"))


@app.get("/api/projects")
def projects() -> dict[str, Any]:
    return {"projects": list_projects(), "active": config["paths"]["output"].name}


@app.post("/api/project")
def switch_project(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Review a different pipeline run without restarting the server."""
    name = str(payload.get("name", ""))
    if name not in {item["name"] for item in list_projects()}:
        raise HTTPException(404, f"找不到 {name}")
    activate(project_dir(name))
    return get_state()


# ---------------------------------------------------------------- state

@app.get("/api/audit")
def audit() -> dict[str, Any]:
    """Judge the captions currently being reviewed, not the ones shipped.

    Recomputing means the verdict follows your edits instead of describing the
    state the pipeline left behind.
    """
    from core.audit import inspect
    paths = config["paths"]
    segments = review.load_state(paths["state"], paths["output"])
    return inspect(segments, config["source"], config["duration"])


def _images() -> list[dict[str, Any]]:
    """Images available for placing, cut-outs before originals."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for folder in IMAGE_DIRS:
        directory = ROOT / folder
        if not directory.is_dir():
            continue
        for image in sorted(directory.glob("*.png")):
            if image.name in seen:
                continue
            seen.add(image.name)
            with Image.open(image) as opened:
                width, height = opened.size
            # A card made with an entrance has a clip beside it. Offering it
            # with the picture is what lets dropping one carry its motion.
            clip = image.with_suffix(".motion.mov")
            found.append({
                "path": f"{folder}/{image.name}",
                "name": image.stem[:40],
                "width": width,
                "height": height,
                "motion": f"{folder}/{clip.name}" if clip.is_file() else None,
                # What sort of thing it is, so a shelf of sixteen can be looked
                # through. A card is known by the page it was drawn from, which
                # is the only honest way to tell one from a photograph.
                "kind": ("cutout" if folder.endswith("cutouts")
                         else "card" if (CARD_DIR / f"{image.stem}.html").is_file()
                         else "picture"),
                "added": int(image.stat().st_mtime),
            })
    return found


def _clips() -> list[dict[str, Any]]:
    """Footage available to place: stock, inserts, anything moving."""
    directory = ROOT / CLIP_DIR
    if not directory.is_dir():
        return []
    found = []
    for clip in sorted(directory.glob("*.mp4")):
        credit = clip.with_suffix(".credit.json")
        details = json.loads(credit.read_text(encoding="utf-8")) if credit.is_file() else {}
        found.append({
            "path": f"{CLIP_DIR}/{clip.name}",
            "name": clip.stem[:40],
            "kind": "clip",
            "added": int(clip.stat().st_mtime),
            # The file, not what the library said about it. Pexels rounds to
            # whole seconds, so an 10.837s clip was offered as 11.0 -- and
            # trimming "to the end" then asked for a sixth of a second that
            # does not exist, which every later piece paid for.
            "seconds": round(media.duration(clip), 3),
            "width": (details.get("size") or [1920, 1080])[0],
            "height": (details.get("size") or [1920, 1080])[1],
            "credit": details.get("author"),
        })
    return found


ASSET_USE = CACHE / "assets.json"     # how often each piece of material is used


def _asset_use() -> dict[str, dict[str, int]]:
    """How often each item has been placed, and when it last was.

    Kept beside the other derived files rather than in the project, because it
    describes working habits and not the video. Losing it costs an ordering,
    not any work.
    """
    if ASSET_USE.is_file():
        try:
            return json.loads(ASSET_USE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


@app.post("/api/asset/used")
def asset_used(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Note that something was placed. Called when one lands on the canvas --
    dragging it and changing your mind is not use."""
    path = str(payload.get("path") or "")
    known = {item["path"] for item in _images()} | {item["path"] for item in _clips()}
    if path not in known:
        raise HTTPException(404, f"找不到素材 {path}")
    ledger = _asset_use()
    entry = ledger.setdefault(path, {"used": 0, "last": 0})
    entry["used"] = int(entry.get("used") or 0) + 1
    entry["last"] = int(time.time())
    ASSET_USE.parent.mkdir(parents=True, exist_ok=True)
    ASSET_USE.write_text(json.dumps(ledger, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    return entry


@app.get("/api/images")
def images() -> dict[str, Any]:
    ledger = _asset_use()

    def stamped(item: dict[str, Any]) -> dict[str, Any]:
        seen = ledger.get(item["path"]) or {}
        return {**item, "used": int(seen.get("used") or 0),
                "last": int(seen.get("last") or 0)}

    return {"images": [stamped(item) for item in _images()],
            "clips": [stamped(item) for item in _clips()]}


@app.get("/media/clipstrip/{name}.png")
def clip_strip(name: str) -> FileResponse:
    """Thumbnails of one placed clip, for drawing inside its bar on the
    timeline. Made on first request and cached like the main filmstrip."""
    wanted = f"{CLIP_DIR}/{name}"
    clip = next((item for item in _clips() if item["path"] == wanted), None)
    if clip is None:
        raise HTTPException(404, f"找不到 {wanted}")
    strip = FILMSTRIP / f"clip_{Path(name).stem}.png"
    media.ensure_filmstrip(ROOT / CLIP_DIR / name, strip,
                           clip["seconds"] or 10.0, every=1.0)
    return FileResponse(strip, media_type="image/png")


@app.get("/media/clip/{name}")
def clip_file(name: str) -> FileResponse:
    """Serve one placeable clip, matched against the listing rather than joined."""
    wanted = f"{CLIP_DIR}/{name}"
    if wanted not in {item["path"] for item in _clips()}:
        raise HTTPException(404, f"找不到 {wanted}")
    return FileResponse(ROOT / CLIP_DIR / name, media_type="video/mp4")


CARD_DIR = ASSETS / "cards"        # the HTML each made picture was made from


@app.get("/api/cards")
def cards() -> dict[str, Any]:
    """The starting points, and anything made from one before.

    A card is designed in HTML because that is what setting type well takes --
    rules, alignment, tabular figures. Keeping the source next to the picture
    means a number can be changed later by editing the number, rather than by
    building the whole card again.
    """
    templates = sorted((CARD_DIR / "templates").glob("*.html"))
    made = sorted(CARD_DIR.glob("*.html"))
    return {
        "templates": [{"name": item.stem, "html": item.read_text(encoding="utf-8")}
                      for item in templates],
        "made": [{"name": item.stem, "html": item.read_text(encoding="utf-8")}
                 for item in made],
    }


@app.post("/api/card")
def make_card(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Render a card's HTML to a transparent PNG in assets/images/, keeping the source."""

    from tools.make_card import capture

    # The name becomes a filename in two directories, so it is checked rather
    # than joined: no separators, no dots, nothing that could climb out.
    name = str(payload.get("name") or "card").strip()
    if not re.fullmatch(r"[\w\u4e00-\u9fff -]{1,48}", name):
        raise HTTPException(400, "名稱只能用中英文、數字、底線、減號和空格")
    html = str(payload.get("html") or "")
    if not html.strip():
        raise HTTPException(400, "沒有內容")

    CARD_DIR.mkdir(parents=True, exist_ok=True)
    page = CARD_DIR / f"{name}.html"
    page.write_text(html, encoding="utf-8")
    # Keeping the source and making the picture are different acts: work is
    # saved often and half-finished, a picture is made when it is ready.
    if not payload.get("render", True):
        return {"saved": f"assets/cards/{page.name}"}

    try:
        target = capture(page, ASSETS / "images" / f"{name}.png")
    except SystemExit as error:                                   # no browser
        raise HTTPException(500, str(error)) from error
    with Image.open(target) as made:
        size = made.size

    made_motion = None
    if payload.get("motion"):
        from core import motion as motion_module
        with Image.open(target) as card:
            report = motion_module.render(html, card, target.with_suffix(".motion.mov"))
        made_motion = {"path": f"assets/images/{target.stem}.motion.mov",
                       "seconds": report["seconds"]}

    return {"saved": f"assets/cards/{page.name}", "path": f"assets/images/{target.name}",
            "width": size[0], "height": size[1], "motion": made_motion}


@app.get("/media/image/{path:path}")
def image_file(path: str) -> FileResponse:
    """Serve a picture the canvas needs: one from the tray, or one the scene
    already refers to. Requests are matched against those two lists rather
    than joined onto a directory, so nothing else on disk can be read."""
    allowed = {item["path"] for item in _images()} | {item["path"] for item in _clips()}
    if config["paths"]["scene"].is_file():
        from core import scene as scene_module
        allowed |= {str(element.get("file"))
                    for element in scene_module.load(config["paths"]["scene"]).get("elements", [])
                    if element.get("file")}
    if path not in allowed:
        raise HTTPException(404, f"找不到 {path}")
    return FileResponse(ROOT / path, media_type="image/png")


def _trim(card: Path) -> tuple[Path, list[int]] | None:
    """A card cropped to what it actually draws, and where that sits.

    Cards are painted on a full 1920x1080 sheet with the artwork off to one
    side -- four fifths of the file is transparent. Trimmed, a card becomes an
    ordinary picture: it can be dragged, resized and thought about, instead of
    being a whole frame that happens to be mostly empty."""
    trimmed = card.with_suffix(".trim.png")
    with Image.open(card) as sheet:
        if sheet.mode != "RGBA":
            return None
        box = sheet.getbbox()               # bounds of everything not transparent
        if not box or box == (0, 0, *sheet.size):
            return None
        if not trimmed.is_file():
            sheet.crop(box).save(trimmed)
    return trimmed, [int(value) for value in box]


def _cards() -> list[dict[str, Any]]:
    """The information cards this run planned, trimmed, with paths relative to
    the project so a scene can be moved or read anywhere."""
    cards = []
    for card in config.get("visuals") or []:
        file = Path(card["file"])
        box = None
        if file.is_file():
            cut = _trim(file)
            if cut:
                file, box = cut
        try:
            file = file.resolve().relative_to(ROOT)
        except ValueError:
            pass
        cards.append({**card, "file": str(file), "box": box})
    return cards


def _default_scene() -> dict[str, Any]:
    """The basic frame: picture upper left, captions under it, channel icon,
    plus whatever cards the run planned."""
    from core import scene as scene_module
    paths = config["paths"]
    srt = "subtitles_bilingual.srt"
    if not (paths["output"] / srt).is_file():
        srt = "subtitles_zh.srt"
    icons = _images()
    scene = scene_module.default_scene(srt, icons[0]["path"] if icons else None)
    scene_module.add_cards(scene, _cards())
    scene["cards_merged"] = True
    return scene


@app.get("/api/scene")
def get_scene() -> dict[str, Any]:
    """The layout being edited, built from defaults on first request."""
    from core import scene as scene_module
    paths = config["paths"]
    if not paths["scene"].is_file():
        scene_module.save(paths["scene"], _default_scene())
    scene = scene_module.load(paths["scene"])
    # Cards used to be pasted on at burn time, so older scenes do not mention
    # them; and the first merge kept them full-frame. Both are fixed once, and
    # the marker means a card you then delete stays deleted.
    merged = scene.get("cards_merged")
    if merged is not CARDS_TRIMMED:
        cards = _cards()
        if not merged:
            scene_module.add_cards(scene, cards)
        else:
            by_id = {f"card{index}": card for index, card in enumerate(cards, start=1)}
            for element in scene["elements"]:
                card = by_id.get(element.get("id"))
                if card and card.get("box"):
                    element["file"], element["box"] = card["file"], list(card["box"])
        scene["cards_merged"] = CARDS_TRIMMED
        scene_module.save(paths["scene"], scene)
    return scene


@app.get("/api/scene/default")
def scene_default() -> dict[str, Any]:
    """The default layout, without touching what is on disk: the editor offers
    it as a starting point and only writes it if you save."""
    return _default_scene()


@app.put("/api/scene")
def put_scene(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Store the layout whole, so one drag is one atomic change."""
    from core import scene as scene_module
    if not isinstance(payload.get("elements"), list):
        raise HTTPException(400, "scene 需要 elements 陣列")
    scene_module.save(config["paths"]["scene"], payload)
    return payload


@app.get("/media/filmstrip.png")
def filmstrip() -> FileResponse:
    """The thumbnails, made on first request rather than at startup: three
    seconds is worth waiting for once, but not before the editor opens."""
    paths = config["paths"]
    # Made from the proxy, not the source: the source is AV1, and decoding it
    # to pull sixty frames took fifteen seconds where the H.264 copy takes
    # three. The proxy exists by the time anything can ask for this.
    config["film"] = media.ensure_filmstrip(paths["proxy"], paths["film"],
                                            config["duration"])
    return FileResponse(paths["film"], media_type="image/png")


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    paths = config["paths"]
    segments = review.load_state(paths["state"], paths["output"])
    total = config["duration"]
    return {
        "project": paths["output"].name,
        "source": config["source"].name,
        "translated": any(item.get("source") for item in segments),
        "duration": total,
        "fps": config.get("fps", 30.0),
        "segments": segments,
        "gaps": review.find_gaps(segments, total),
        "visuals": config["visuals"],
        "peaks": config["peaks"],
        "film": {"every": media.FILM_EVERY, "height": media.FILM_HEIGHT},
        # Changes whenever the video does, and the browser hangs it on the end
        # of the proxy and filmstrip URLs. Without it, re-assembling under the
        # same name kept the same URLs and the browser reused the pictures it
        # already had -- a timeline drawn from a video that no longer exists.
        "stamp": cache_key(config["source"]),
        "finished": finished_videos(),
        "reviewedSrt": str(paths["reviewed_srt"].relative_to(ROOT)),
        "hasReviewedSrt": paths["reviewed_srt"].is_file(),
    }


@app.put("/api/state")
def put_state(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise HTTPException(400, "segments must be a non-empty list")
    paths = config["paths"]
    result = review.save_state(paths["state"], paths["reviewed_srt"], segments)
    total = config["duration"]
    return {
        "segments": result["segments"],
        "gaps": review.find_gaps(result["segments"], total),
        "written": sorted(Path(p).name for p in result["written"].values()),
        "lines": result["lines"],
    }


@app.get("/api/waveform")
def waveform() -> dict[str, Any]:
    return {"peaks": config["peaks"], "duration": config["duration"]}


# ---------------------------------------------------------------- re-listen

def _whisper(model_name: str):
    if model_name not in _whisper_models:
        from faster_whisper import WhisperModel
        _whisper_models[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
    return _whisper_models[model_name]


@app.post("/api/relisten")
def relisten(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Re-transcribe one window only, optionally in sensitive mode.

    Running sensitive mode over the whole video adds errors, but over a single
    street-interview window it recovers speech the default pass misses.
    """
    start = max(0.0, float(payload.get("start", 0)))
    end = min(config["duration"], float(payload.get("end", 0)))
    if end - start < 0.2:
        raise HTTPException(400, "window too short")
    sensitive = bool(payload.get("sensitive", True))
    model_name = str(payload.get("model", "medium"))

    clip = media.slice_audio(config["source"], start, end, SCRATCH / "relisten.wav")
    raw_segments, _ = _whisper(model_name).transcribe(
        str(clip),
        beam_size=5,
        vad_filter=not sensitive,
        condition_on_previous_text=False,
        no_speech_threshold=0.3 if sensitive else 0.6,
        log_prob_threshold=-1.5 if sensitive else -1.0,
    )
    converter = OpenCC("s2twp")
    found = []
    for segment in raw_segments:
        text = converter.convert(segment.text.strip())
        if text:
            found.append({
                "start": round(start + segment.start, 2),
                "end": round(start + segment.end, 2),
                "text": text,
                "logprob": round(getattr(segment, "avg_logprob", 0.0), 3),
            })
    return {"segments": found, "window": {"start": start, "end": end},
            "model": model_name, "sensitive": sensitive}


# ---------------------------------------------------------------- burn

def _burn_worker(segments: list[dict[str, Any]], variant: str) -> None:
    """Burn the whole video the way the editor has been showing it.

    Everything the run has produced lands here: the corrected subtitles, drawn
    as the same pictures the canvas previews, the layout from scene.json, and
    any cards the pipeline planned. Before this, a full burn only knew about
    subtitles -- the layout you arranged simply was not in the finished file."""
    from core import caption as caption_module
    from core import compose as compose_module
    from core import scene as scene_module

    paths = config["paths"]
    try:
        written = review.save_state(paths["state"], paths["reviewed_srt"], segments)["paths"]
        chosen = written.get(variant) or written["zh"]

        from core import cuts as cuts_module
        scene = get_scene()
        removed = cuts_module.tidy(scene.get("cuts") or [])
        element = scene_module.one(scene, "subtitle")
        listing = None
        if element:
            # The bilingual switch decides which of the saved files is drawn.
            element["srt"] = chosen.name
            built = _draw_captions(scene)
            scene["caption_band"] = built["band"]
            listing = caption_module.playlist(
                built["captions"], cuts_module.duration_after(removed, config["duration"]),
                built["band"], paths["captions"],
            )

        compose_module.compose(
            config["source"], scene, paths["reviewed_mp4"],
            srt_dir=paths["output"], image_root=ROOT,
            captions=listing, progress=paths["burn_progress"],
        )
        with _burn_lock:
            _burn.update(state="done",
                         message=f'完成：{paths["output"].name}/{paths["reviewed_mp4"].name}',
                         output=str(paths["reviewed_mp4"]))
    except Exception as error:                                    # noqa: BLE001
        traceback.print_exc()
        with _burn_lock:
            _burn.update(state="error", message=str(error), output=None)


def _caption_srt(scene: dict[str, Any]) -> tuple[dict[str, Any] | None, Path | None]:
    """The subtitle element and the file it should be drawn from -- whatever
    the editor last saved, falling back to what the pipeline produced."""
    from core import scene as scene_module
    element = scene_module.one(scene, "subtitle")
    if not element:
        return None, None
    output = config["paths"]["output"]
    name = element.get("srt", "subtitles_zh.srt")
    reviewed = output / name.replace(".srt", ".reviewed.srt")
    return element, (reviewed if reviewed.is_file() else output / name)


def _draw_captions(scene: dict[str, Any] | None = None) -> dict[str, Any]:
    """Draw every caption as a picture. Cheap after the first time: the file
    name is a hash of the text and the styling, so only what changed is drawn.

    The cues come back on the finished video's clock, with anything inside a
    cut dropped -- the pictures are what gets overlaid, so they have to agree
    with the frames that survive."""
    from core import caption as caption_module
    from core import cuts as cuts_module
    from core import scene as scene_module

    scene = scene if scene is not None else get_scene()
    element, srt = _caption_srt(scene)
    if not element or not srt or not srt.is_file():
        raise HTTPException(400, "這個版面沒有字幕元件，或找不到字幕檔")
    removed = cuts_module.tidy(scene.get("cuts") or [])
    cues = cuts_module.apply_to_cues(caption_module.read_srt(srt), removed)
    built = caption_module.build(
        cues, element,
        tuple(scene.get("canvas", scene_module.CANVAS)), config["paths"]["captions"],
    )
    built["source"] = srt.name
    built["cut"] = round(sum(end - start for start, end in removed), 2)
    return built


@app.post("/api/captions")
def captions() -> dict[str, Any]:
    return _draw_captions()


@app.get("/media/caption/{name}")
def caption_file(name: str) -> FileResponse:
    path = config["paths"]["captions"] / name
    if not path.is_file() or not name.startswith("cap_") or not name.endswith(".png"):
        raise HTTPException(404, f"找不到 {name}")
    return FileResponse(path, media_type="image/png")


PREVIEW_SECONDS = 10.0


@app.post("/api/preview")
def preview(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Burn a short clip through the same composer the final render uses, so
    what the browser draws can be checked against what ffmpeg produces."""
    from core import compose as compose_module
    from core import scene as scene_module

    paths = config["paths"]
    if not paths["scene"].is_file():
        get_scene()
    scene = scene_module.load(paths["scene"])

    start = max(0.0, float(payload.get("start") or 0.0))
    seconds = float(payload.get("seconds") or PREVIEW_SECONDS)
    clip = paths["preview_clip"]
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(config["source"]),
         "-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "128k", str(clip)],
        check=True,
    )
    # Captions are pictures, and the clip starts at `start`, so the playlist is
    # written against the clip's own clock.
    # The clip was cut out of the source, so the cuts have to move onto its
    # own clock before anything is composed against them.
    from core import caption as caption_module
    from core import cuts as cuts_module
    window = [[max(0.0, cut_start - start), min(seconds, cut_end - start)]
              for cut_start, cut_end in cuts_module.tidy(scene.get("cuts") or [])
              if cut_end > start and cut_start < start + seconds]
    scene["cuts"] = window

    # Timed elements are on the source's clock too. Without this a card at
    # 0:13 keeps asking to appear thirteen seconds into a clip that begins
    # there -- which is to say, never.
    shifted = []
    for element in scene["elements"]:
        if element.get("from") is None:
            shifted.append(element)
            continue
        from_at = float(element["from"]) - start
        to_at = float(element.get("to", element["from"])) - start
        if to_at <= 0 or from_at >= seconds:
            continue
        shifted.append({**element, "from": max(0.0, from_at), "to": min(seconds, to_at)})
    scene["elements"] = shifted

    listing = None
    element = scene_module.one(scene, "subtitle")
    if element:
        clipped = {**scene, "cuts": []}          # cues are shifted here, not twice
        built = _draw_captions(clipped)
        scene["caption_band"] = built["band"]
        # Only what falls inside the window, or a caption from elsewhere in the
        # video would be shifted to a negative time, land on zero, and play
        # over the opening of the preview.
        inside = [cue for cue in built["captions"]
                  if cue["end"] > start and cue["start"] < start + seconds]
        moved = cuts_module.apply_to_cues(
            [{**cue,
              "start": max(0.0, cue["start"] - start),
              "end": min(seconds, cue["end"] - start)}
             for cue in inside],
            cuts_module.tidy(window),
        )
        listing = caption_module.playlist(
            moved, cuts_module.duration_after(cuts_module.tidy(window), seconds),
            built["band"], paths["captions"],
        )

    out = paths["preview"]
    compose_module.compose(clip, scene, out, srt_dir=paths["output"],
                           image_root=ROOT, captions=listing)
    return {"file": out.name, "start": start, "seconds": seconds}


@app.get("/media/preview/{name}")
def preview_file(name: str) -> FileResponse:
    path = PREVIEWS / name
    if not path.is_file() or "/" in name or not name.endswith(".mp4"):
        raise HTTPException(404, f"找不到 {name}")
    return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})


@app.post("/api/burn")
def burn(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    with _burn_lock:
        if _burn["state"] == "running":
            return dict(_burn)
        _burn.update(state="running", message="重新燒錄中（版面 + 校對字幕 + 圖卡）",
                     output=None, percent=0, seconds=0.0, started=time.time())
        # A stale progress file would report the previous burn's position.
        progress = config["paths"]["burn_progress"]
        if progress.is_file():
            progress.unlink()
    segments = payload.get("segments") or review.load_state(config["paths"]["state"], config["paths"]["output"])
    variant = "bilingual" if payload.get("bilingual") else "zh"
    threading.Thread(target=_burn_worker, args=(segments, variant), daemon=True).start()
    return dict(_burn)


def _burn_position() -> float:
    """Seconds of video ffmpeg has written, from the file it keeps updating."""
    path = config["paths"].get("burn_progress")
    if not path or not path.is_file():
        return 0.0
    try:
        # The file is appended to, so the last out_time_us wins.
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return 0.0
    for line in reversed(lines):
        key, _, value = line.partition("=")
        if key == "out_time_us" and value.strip().isdigit():
            return int(value) / 1_000_000
    return 0.0


@app.get("/api/burn")
def burn_status() -> dict[str, Any]:
    with _burn_lock:
        status = dict(_burn)
    if status["state"] != "running":
        return status
    total = config.get("duration") or 0.0
    done = _burn_position()
    status["seconds"] = round(done, 1)
    status["percent"] = round(min(99, done / total * 100)) if total else 0
    elapsed = time.time() - (status.get("started") or time.time())
    # Encoding runs at a steady rate, so elapsed-per-second extrapolates well.
    if done > 2 and elapsed > 2:
        status["remaining"] = round(max(0.0, (total - done) * (elapsed / done)))
    return status


@app.exception_handler(Exception)
def unhandled(request, error: Exception) -> JSONResponse:          # noqa: ANN001
    traceback.print_exc()
    return JSONResponse({"detail": str(error)}, status_code=500)


# ---------------------------------------------------------------- startup

def activate(output: Path, source: Path | None = None) -> None:
    """Point the editor at one pipeline run, preparing its media if needed."""
    for drawer in (PROXY, WAVEFORM, REVIEWS, CAPTIONS, PREVIEWS, SCRATCH, FILMSTRIP):
        drawer.mkdir(parents=True, exist_ok=True)
    source = source or find_source(output)
    paths = paths_for(output, source)
    config["paths"] = paths
    config["source"] = source
    config["duration"] = media.duration(source)
    config["fps"] = _frame_rate(source)

    print(f"校對 {output.name}（{source.name}）")
    if not paths["proxy"].is_file():
        print("      準備瀏覽器可播放的 proxy（僅第一次需要轉檔，約 45 秒）")
    media.ensure_proxy(source, paths["proxy"])
    config["peaks"] = media.ensure_waveform(source, paths["peaks"])

    visuals_path = output / "ai_visuals.json"
    config["visuals"] = json.loads(visuals_path.read_text(encoding="utf-8")) if visuals_path.is_file() else []
    print(f"      字幕就緒、波形 {len(config['peaks'])} 點、圖卡 {len(config['visuals'])} 張")


def main() -> None:
    parser = argparse.ArgumentParser(description="字幕校對網頁（不改動 pipeline）")
    parser.add_argument("source", nargs="?", help="原始影片，預設抓 work/source_*.mp4 最新的一支")
    parser.add_argument("--project", "--output", dest="project", default=None,
                        help="要打開哪個專案，預設最近改過的那個")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    known = list_projects()
    if not known:
        raise SystemExit(f"{PROJECTS} 裡沒有專案。"
                         "一個專案就是一個資料夾，裡面有 run.json 說明它是哪支影片。")
    # No name given opens the one worked on last, which is nearly always the
    # one meant. A name that is not there says which ones are.
    if args.project is None:
        chosen = max(known, key=lambda item: item["modified"])["name"]
    else:
        chosen = Path(args.project).name        # tolerate a pasted path
        if chosen not in {item["name"] for item in known}:
            raise SystemExit(f"找不到專案 {chosen}\n"
                             f"有的是：{', '.join(item['name'] for item in known)}")
    output = project_dir(chosen)

    activate(output, Path(args.source).resolve() if args.source else None)

    import uvicorn
    print(f"\n開啟 http://127.0.0.1:{args.port}\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
