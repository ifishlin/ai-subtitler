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

from fastapi import Body, FastAPI, HTTPException                      # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from opencc import OpenCC                                            # noqa: E402

from subtitle_editor import media, review                            # noqa: E402
from subtitle_editor.srt import write_srt                            # noqa: E402

OUTPUT = ROOT / "output"
WORK = ROOT / "work"
CACHE = ROOT / "editor_cache"
STATIC = Path(__file__).resolve().parent / "static"

PROXY = CACHE / "proxy.mp4"
PEAKS = CACHE / "peaks.json"
STATE = CACHE / "review.json"
REVIEWED_SRT = OUTPUT / "subtitles_zh.reviewed.srt"
REVIEWED_MP4 = OUTPUT / "final_reviewed.mp4"

app = FastAPI(title="Subtitle Review")
config: dict[str, Any] = {}
_whisper_models: dict[str, Any] = {}
_burn: dict[str, Any] = {"state": "idle", "message": "", "output": None}
_burn_lock = threading.Lock()


def find_source() -> Path:
    candidates = sorted(WORK.glob("source_*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit(f"No source video found in {WORK}")
    return candidates[0]


# ---------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/media/proxy.mp4")
def proxy() -> FileResponse:
    # FileResponse honours HTTP Range, which is what lets the browser seek.
    return FileResponse(PROXY, media_type="video/mp4")


# ---------------------------------------------------------------- state

@app.get("/api/state")
def get_state() -> dict[str, Any]:
    segments = review.load_state(STATE, OUTPUT)
    total = config["duration"]
    return {
        "source": config["source"].name,
        "duration": total,
        "segments": segments,
        "gaps": review.find_gaps(segments, total),
        "visuals": config["visuals"],
        "reviewedSrt": str(REVIEWED_SRT.relative_to(ROOT)),
        "hasReviewedSrt": REVIEWED_SRT.is_file(),
    }


@app.put("/api/state")
def put_state(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        raise HTTPException(400, "segments must be a non-empty list")
    result = review.save_state(STATE, REVIEWED_SRT, segments)
    total = config["duration"]
    return {
        "segments": result["segments"],
        "gaps": review.find_gaps(result["segments"], total),
        "written": result["written"],
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

def _burn_worker(segments: list[dict[str, Any]]) -> None:
    from src.render import render
    try:
        write_srt(REVIEWED_SRT, segments)
        render(config["source"], REVIEWED_SRT, config["visuals"], REVIEWED_MP4)
        with _burn_lock:
            _burn.update(state="done", message=f"完成：{REVIEWED_MP4.name}", output=str(REVIEWED_MP4))
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
    segments = payload.get("segments") or review.load_state(STATE, OUTPUT)
    threading.Thread(target=_burn_worker, args=(segments,), daemon=True).start()
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

def prepare(source: Path) -> None:
    import json

    print(f"來源影片：{source.name}")
    config["source"] = source
    config["duration"] = media.duration(source)

    print("[1/2] 準備瀏覽器可播放的 proxy（僅第一次需要轉檔）")
    media.ensure_proxy(source, PROXY)
    print(f"      {PROXY.relative_to(ROOT)}  {PROXY.stat().st_size / 1e6:.1f} MB")

    print("[2/2] 計算波形")
    config["peaks"] = media.ensure_waveform(source, PEAKS)

    visuals_path = OUTPUT / "ai_visuals.json"
    config["visuals"] = json.loads(visuals_path.read_text(encoding="utf-8")) if visuals_path.is_file() else []
    print(f"      波形 {len(config['peaks'])} 點、圖卡 {len(config['visuals'])} 張")


def main() -> None:
    parser = argparse.ArgumentParser(description="字幕校對網頁（不改動 pipeline）")
    parser.add_argument("source", nargs="?", help="原始影片，預設抓 work/source_*.mp4 最新的一支")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    prepare(Path(args.source).resolve() if args.source else find_source())

    import uvicorn
    print(f"\n開啟 http://127.0.0.1:{args.port}\n")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
