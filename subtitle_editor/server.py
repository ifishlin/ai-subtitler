"""Local subtitle proofreading server.

Read-only with respect to the pipeline: it reads output/ and work/, and writes
only output/subtitles_zh.reviewed.srt, output/final_reviewed.mp4 and
editor_cache/. Nothing in main.py or src/ is modified or re-run.

    .venv/bin/python subtitle_editor/server.py
    open http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import json                                                       # noqa: E402

from fastapi import Body, FastAPI, HTTPException                      # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from opencc import OpenCC                                            # noqa: E402

from subtitle_editor import media, review                            # noqa: E402
from subtitle_editor.srt import write_srt                            # noqa: E402

WORK = ROOT / "work"
CACHE = ROOT / "editor_cache"
STATIC = Path(__file__).resolve().parent / "static"

# Everything that depends on which run is being reviewed lives in config, so
# the browser can switch runs without restarting the server. Media derivatives
# are keyed by video, so switching back to a run costs nothing the second time.
def paths_for(output: Path, source: Path | None = None) -> dict[str, Path]:
    stem = source.stem if source else "none"
    return {
        "output": output,
        "state": CACHE / f"review_{output.name}.json",
        "reviewed_srt": output / "subtitles_zh.reviewed.srt",
        "reviewed_mp4": output / "final_reviewed.mp4",
        "proxy": CACHE / f"proxy_{stem}.mp4",
        "peaks": CACHE / f"peaks_{stem}.json",
    }

app = FastAPI(title="Subtitle Review")
config: dict[str, Any] = {}
_whisper_models: dict[str, Any] = {}
_burn: dict[str, Any] = {"state": "idle", "message": "", "output": None}
_burn_lock = threading.Lock()


def list_projects() -> list[dict[str, Any]]:
    """Every pipeline output directory that holds subtitles to review."""
    found = []
    for directory in sorted(ROOT.glob("output*")):
        srt = directory / "subtitles_zh.srt"
        if not (directory.is_dir() and srt.is_file()):
            continue
        run = directory / "run.json"
        details = json.loads(run.read_text(encoding="utf-8")) if run.is_file() else {}
        found.append({
            "name": directory.name,
            "lines": srt.read_text(encoding="utf-8").count("-->"),
            "source": Path(details.get("source", "")).name,
            "fillGaps": bool(details.get("fill_gaps")),
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
            f"work/ 裡有 {names}。請重跑 main.py，或手動建立 "
            f"{output.name}/run.json 寫入 {{\"source\": \"work/影片檔名.mp4\"}}。",
        )
    print(f"提醒：{output.name}/run.json 不存在，只有一支影片，使用 {candidates[0].name}")
    return candidates[0]


# ---------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


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


@app.get("/api/projects")
def projects() -> dict[str, Any]:
    return {"projects": list_projects(), "active": config["paths"]["output"].name}


@app.post("/api/project")
def switch_project(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Review a different pipeline run without restarting the server."""
    name = str(payload.get("name", ""))
    if name not in {item["name"] for item in list_projects()}:
        raise HTTPException(404, f"找不到 {name}")
    activate(ROOT / name)
    return get_state()


# ---------------------------------------------------------------- state

@app.get("/api/audit")
def audit() -> dict[str, Any]:
    """Judge the captions currently being reviewed, not the ones shipped.

    Recomputing means the verdict follows your edits instead of describing the
    state the pipeline left behind.
    """
    from src.audit import inspect
    paths = config["paths"]
    segments = review.load_state(paths["state"], paths["output"])
    return inspect(segments, config["source"], config["duration"])


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
        "segments": segments,
        "gaps": review.find_gaps(segments, total),
        "visuals": config["visuals"],
        "peaks": config["peaks"],
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

    clip = media.slice_audio(config["source"], start, end, CACHE / "relisten.wav")
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
    from src.render import render
    paths = config["paths"]
    try:
        written = review.save_state(paths["state"], paths["reviewed_srt"], segments)["paths"]
        # render() picks its subtitle styling from the filename, so a bilingual
        # burn has to be handed the bilingual file rather than a copy.
        chosen = written.get(variant) or written["zh"]
        render(config["source"], chosen, config["visuals"], paths["reviewed_mp4"])
        with _burn_lock:
            _burn.update(state="done",
                         message=f'完成：{paths["output"].name}/{paths["reviewed_mp4"].name}',
                         output=str(paths["reviewed_mp4"]))
    except Exception as error:                                    # noqa: BLE001
        traceback.print_exc()
        with _burn_lock:
            _burn.update(state="error", message=str(error), output=None)


@app.post("/api/burn")
def burn(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    with _burn_lock:
        if _burn["state"] == "running":
            return dict(_burn)
        _burn.update(state="running", message="重新燒錄中（原片 + 校對字幕 + 既有圖卡）", output=None)
    segments = payload.get("segments") or review.load_state(config["paths"]["state"], config["paths"]["output"])
    variant = "bilingual" if payload.get("bilingual") else "zh"
    threading.Thread(target=_burn_worker, args=(segments, variant), daemon=True).start()
    return dict(_burn)


@app.get("/api/burn")
def burn_status() -> dict[str, Any]:
    with _burn_lock:
        return dict(_burn)


@app.exception_handler(Exception)
def unhandled(request, error: Exception) -> JSONResponse:          # noqa: ANN001
    traceback.print_exc()
    return JSONResponse({"detail": str(error)}, status_code=500)


# ---------------------------------------------------------------- startup

def activate(output: Path, source: Path | None = None) -> None:
    """Point the editor at one pipeline run, preparing its media if needed."""
    CACHE.mkdir(parents=True, exist_ok=True)
    source = source or find_source(output)
    paths = paths_for(output, source)
    config["paths"] = paths
    config["source"] = source
    config["duration"] = media.duration(source)

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
    parser.add_argument("--output", default="output", help="要校對的 pipeline 輸出目錄")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    output = (ROOT / args.output).resolve()
    if not (output / "subtitles_zh.srt").is_file():
        available = ", ".join(item["name"] for item in list_projects()) or "（沒有）"
        raise SystemExit(f"找不到 {output / 'subtitles_zh.srt'}\n可用的目錄：{available}")

    activate(output, Path(args.source).resolve() if args.source else None)

    import uvicorn
    print(f"\n開啟 http://127.0.0.1:{args.port}\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
