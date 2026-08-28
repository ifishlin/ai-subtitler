"""Local subtitle proofreading server.

Read-only with respect to the pipeline: it reads output/ and work/, and writes
only output/subtitles_zh.reviewed.srt, output/final_reviewed.mp4 and
editor_cache/. Nothing in produce.py or core/ is modified or re-run.

    .venv/bin/python studio/server.py
    open http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import json                                                       # noqa: E402

from fastapi import Body, FastAPI, HTTPException                      # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from opencc import OpenCC                                            # noqa: E402
from PIL import Image                                               # noqa: E402

from studio import media, review                            # noqa: E402
from studio.srt import write_srt                            # noqa: E402

WORK = ROOT / "work"
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
IMAGE_DIRS = ("img_cut", "img")     # searched in order; cut-outs preferred
CLIP_DIR = "clips"                  # footage to lay over the frame
CARDS_TRIMMED = 2                   # scenes carrying cards cropped to their art


def paths_for(output: Path, source: Path | None = None) -> dict[str, Path]:
    stem = source.stem if source else "none"
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

app = FastAPI(title="Subtitle Review")
config: dict[str, Any] = {}
_whisper_models: dict[str, Any] = {}
_burn: dict[str, Any] = {"state": "idle", "message": "", "output": None,
                         "percent": 0, "seconds": 0.0, "started": 0.0}
_burn_lock = threading.Lock()


def _frame_rate(video: Path) -> float:
    """Frames per second, so the editor can step one frame at a time."""
    import subprocess
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
            "seconds": float(details.get("duration") or 0.0),
            "width": (details.get("size") or [1920, 1080])[0],
            "height": (details.get("size") or [1920, 1080])[1],
            "credit": details.get("author"),
        })
    return found


@app.get("/api/images")
def images() -> dict[str, Any]:
    return {"images": _images(), "clips": _clips()}


@app.get("/media/clip/{name}")
def clip_file(name: str) -> FileResponse:
    """Serve one placeable clip, matched against the listing rather than joined."""
    wanted = f"{CLIP_DIR}/{name}"
    if wanted not in {item["path"] for item in _clips()}:
        raise HTTPException(404, f"找不到 {wanted}")
    return FileResponse(ROOT / CLIP_DIR / name, media_type="video/mp4")


CARD_DIR = ROOT / "cards"          # the HTML each made picture was made from


@app.get("/api/cards")
def cards() -> dict[str, Any]:
    """The starting points, and anything made from one before.

    A card is designed in HTML because that is what setting type well takes --
    rules, alignment, tabular figures. Keeping the source next to the picture
    means a number can be changed later by editing the number, rather than by
    building the whole card again.
    """
    templates = sorted((ROOT / "cards" / "templates").glob("*.html"))
    made = sorted(CARD_DIR.glob("*.html"))
    return {
        "templates": [{"name": item.stem, "html": item.read_text(encoding="utf-8")}
                      for item in templates],
        "made": [{"name": item.stem, "html": item.read_text(encoding="utf-8")}
                 for item in made],
    }


@app.post("/api/card")
def make_card(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Render a card's HTML to a transparent PNG in img/, keeping the source."""
    import re

    from make_card import capture

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
        return {"saved": f"cards/{page.name}"}

    try:
        target = capture(page, ROOT / "img" / f"{name}.png")
    except SystemExit as error:                                   # no browser
        raise HTTPException(500, str(error)) from error
    with Image.open(target) as made:
        size = made.size

    made_motion = None
    if payload.get("motion"):
        from core import motion as motion_module
        with Image.open(target) as card:
            report = motion_module.render(html, card, target.with_suffix(".motion.mov"))
        made_motion = {"path": f"img/{target.stem}.motion.mov",
                       "seconds": report["seconds"]}

    return {"saved": f"cards/{page.name}", "path": f"img/{target.name}",
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
    import subprocess
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
