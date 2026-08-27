"""Burn a scene onto a video.

Reads the same scene.json the browser lays out, so the render is the preview.
Elements are drawn in the order they appear, which is what decides overlap: an
explanatory image listed after the video sits on top of it, and can extend past
its edge onto the field.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import scene as scene_module
from .utils import run


def _filter_path(path: Path) -> str:
    """A path as an ffmpeg filter argument: backslashes, quotes and the colon
    that would otherwise start the next option all have to be escaped."""
    return str(path.resolve()).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")


def _hex_to_ffmpeg(value: str) -> str:
    return "0x" + value.lstrip("#").upper()


def compose(
    video: Path,
    scene: dict[str, Any],
    output: Path,
    srt_dir: Path | None = None,
    image_root: Path | None = None,
    captions: Path | None = None,
    progress: Path | None = None,
) -> Path:
    """Render `video` into `scene` and burn the result to `output`.

    With `captions` -- a concat list from src/caption.py -- the subtitles are
    overlaid as the pictures the editor previewed, rather than typeset again by
    libass. That is the only way the preview and the burn can be the same:
    otherwise two typesetters lay out the same string and disagree.

    With `progress`, ffmpeg writes its position to that file as it goes, which
    is how a burn that takes minutes can say how far it has got."""
    canvas = tuple(scene.get("canvas", scene_module.CANVAS))
    background = _hex_to_ffmpeg(scene.get("background", scene_module.BACKGROUND))
    elements = scene.get("elements", [])
    srt_dir = srt_dir or output.parent
    image_root = image_root or Path.cwd()
    output.parent.mkdir(parents=True, exist_ok=True)

    # Inputs: the video first, then one for each image, in element order so the
    # stream index can be derived from position rather than tracked separately.
    command = ["ffmpeg", "-y", "-i", str(video)]
    image_inputs: dict[str, int] = {}
    index = 1
    caption_stream = None
    caption_band = list(scene.get("caption_band") or [0, 0, canvas[0], canvas[1]])
    if captions and captions.is_file():
        # One stream of stills with durations: a four-minute video has more
        # captions than ffmpeg accepts as separate inputs.
        command.extend(["-f", "concat", "-safe", "0", "-i", str(captions)])
        caption_stream = index
        index += 1
    for element in elements:
        if element.get("type") != "image":
            continue
        path = Path(element["file"])
        if not path.is_absolute():
            path = image_root / path
        command.extend(["-loop", "1", "-i", str(path)])
        image_inputs[element["id"]] = index
        index += 1

    filters = [f"color=c={background}:s={canvas[0]}x{canvas[1]}:r=30[bg]"]
    previous = "bg"
    step = 0

    for element in elements:
        kind = element.get("type")
        current = f"s{step}"

        if kind == "video":
            width, height = scene_module.size_of(element)
            left, top = element["box"][0], element["box"][1]
            filters.append(f"[0:v]scale={width}:{height},setsar=1[pic]")
            filters.append(f"[{previous}][pic]overlay={left}:{top}:shortest=1[{current}]")

        elif kind == "image":
            width, height = scene_module.size_of(element)
            left, top = element["box"][0], element["box"][1]
            stream = image_inputs[element["id"]]
            window = scene_module.timed(element)
            gate = f":enable='between(t,{window[0]},{window[1]})'" if window else ""
            filters.append(f"[{stream}:v]format=rgba,scale={width}:{height}[img{step}]")
            filters.append(f"[{previous}][img{step}]overlay={left}:{top}{gate}[{current}]")

        elif kind == "subtitle":
            if caption_stream is not None:
                # The strip carries its own position; it only has to be laid
                # down where src/caption.py said it belongs.
                filters.append(f"[{caption_stream}:v]fps=30,format=rgba[cap]")
                filters.append(
                    f"[{previous}][cap]overlay={caption_band[0]}:{caption_band[1]}"
                    f":shortest=1[{current}]"
                )
            else:
                srt = srt_dir / element["srt"]
                if not srt.is_file():
                    continue
                style = scene_module.subtitle_style(element, canvas)
                filters.append(
                    f"[{previous}]subtitles=filename='{_filter_path(srt)}'"
                    f":force_style='{style}'[{current}]"
                )
        else:
            continue

        previous = current
        step += 1

    if progress:
        command.extend(["-progress", str(progress), "-nostats"])
    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", f"[{previous}]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        "-shortest", str(output),
    ])
    run(command)
    return output
