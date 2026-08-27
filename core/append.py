"""Append a clip to the end of a finished video.

Stock footage rarely matches the programme it is joined to -- different
resolution, frame rate, pixel format, and often no audio at all -- so each
piece is normalised to the first video's specification before concatenation.
Re-encoding both is what makes the join seamless; stream copying would need
the sources to already agree.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


def probe(video: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-show_entries", "format=duration", "-of", "json", str(video)],
        check=True, text=True, capture_output=True,
    )
    data = json.loads(result.stdout)
    stream = (data.get("streams") or [{}])[0]
    numerator, _, denominator = (stream.get("r_frame_rate") or "30/1").partition("/")
    fps = float(numerator) / float(denominator or 1)
    return {
        "width": int(stream.get("width", 1920)),
        "height": int(stream.get("height", 1080)),
        "fps": round(fps, 3),
        "duration": float(data.get("format", {}).get("duration", 0)),
    }


def caption_image(
    text: str, size: tuple[int, int], font_path: Path, out: Path
) -> Path:
    """A lower-third caption to place over the appended clip."""
    width, height = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    band = int(height * 0.16)
    draw.rectangle((0, height - band, width, height), fill=(9, 30, 48, 210))
    font = ImageFont.truetype(str(font_path), size=int(height * 0.055))
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((width - (box[2] - box[0])) / 2, height - band + (band - (box[3] - box[1])) / 2 - box[1]),
        text, font=font, fill="white",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def append_clip(
    video: Path,
    clip: Path,
    output: Path,
    seconds: float = 10.0,
    caption: str | None = None,
    font: Path = Path("/System/Library/Fonts/PingFang.ttc"),
    fade: float = 0.5,
    mute_clip: bool = True,
) -> Path:
    """Join `seconds` of `clip` onto the end of `video`.

    The clip is scaled to fit without distortion and padded, so vertical or
    differently proportioned footage keeps its shape instead of stretching.
    """
    spec = probe(video)
    width, height, fps = spec["width"], spec["height"], spec["fps"]
    output.parent.mkdir(parents=True, exist_ok=True)

    overlay = None
    if caption:
        overlay = caption_image(caption, (width, height), font,
                                output.parent / f"{output.stem}_caption.png")

    inputs = ["-i", str(video), "-t", f"{seconds:.3f}", "-i", str(clip)]
    if overlay:
        inputs += ["-loop", "1", "-t", f"{seconds:.3f}", "-i", str(overlay)]

    # Scale down to fit, pad out to the exact frame, and match frame rate and
    # pixel format; concat demands every input agree on all of them.
    tail = (
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps},format=yuv420p"
    )
    filters = [f"[0:v]setsar=1,fps={fps},format=yuv420p[main]"]
    if overlay:
        filters.append(f"{tail}[bg]")
        filters.append(f"[2:v]format=rgba[cap];[bg][cap]overlay=0:0[tailv]")
    else:
        filters.append(f"{tail}[tailv]")
    if fade > 0:
        filters.append(f"[tailv]fade=t=in:st=0:d={fade:.2f}[tailf]")
        tail_label = "tailf"
    else:
        tail_label = "tailv"

    # Stock footage often carries no audio track, so synthesise silence rather
    # than letting concat fail or drop the programme's own sound.
    filters.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{seconds:.3f}[taila]"
                   if mute_clip else f"[1:a]aresample=48000[taila]")
    filters.append("[0:a:0]aresample=48000[maina]")
    filters.append(f"[main][maina][{tail_label}][taila]concat=n=2:v=1:a=1[v][a]")

    command = [
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)
    return output
