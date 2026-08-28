"""Joining pieces of video into one, with their subtitles.

Until now a run had one source and everything was laid over it, so the finished
video could never be longer than what came out of the camera. Appending
anything -- a second interview, a clarification cut into the middle, a closing
card -- means the timeline stops belonging to a video and starts belonging to
the assembly.

The model is a list rather than a graph: each piece names a source, which part
of it to take, and its own subtitles. Order is order. Inserting is not a
separate idea from appending -- it is appending after splitting the piece you
are inserting into -- and a cut is a piece left out. One shape covers all
three, which is why it is worth changing the model rather than adding a
feature to the old one.

What comes out is an ordinary run: a video file and an SRT, in a directory the
editor opens like any other. Everything downstream -- layout, cards, cutting,
burning -- carries on unchanged, because to it this is simply a video that
happens to have been made here rather than downloaded.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from .utils import timestamp

TIME_LINE = re.compile(
    r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})"
)
# One shape for everything, so a piece from a phone and a piece from a stock
# library can sit next to each other without the seam showing.
WIDTH, HEIGHT, FPS, RATE = 1920, 1080, 30, 48000
SLIVER = 0.35   # a caption shorter than this at a seam is dropped


def _seconds(hours: str, minutes: str, secs: str, millis: str) -> float:
    return (int(hours) * 3600 + int(minutes) * 60 + int(secs)
            + int(millis.ljust(3, "0")) / 1000)


def read_cues(path: Path) -> list[dict[str, Any]]:
    """Cues with their line breaks intact, so a bilingual caption stays two lines."""
    if not path or not Path(path).is_file():
        return []
    cues = []
    for block in re.split(r"\n\s*\n", Path(path).read_text(encoding="utf-8").strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        found = next(((i, TIME_LINE.search(l)) for i, l in enumerate(lines)
                      if TIME_LINE.search(l)), None)
        if not found:
            continue
        index, match = found
        cues.append({
            "start": _seconds(*match.groups()[:4]),
            "end": _seconds(*match.groups()[4:]),
            "text": "\n".join(line.strip() for line in lines[index + 1:]),
        })
    return cues


def merge_cues(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every piece's captions, moved onto the finished video's clock.

    A caption straddling the edge of a piece is trimmed to it: the words that
    are still on screen belong, and the ones spoken in the part left out do
    not.
    """
    merged: list[dict[str, Any]] = []
    clock = 0.0
    for piece in pieces:
        start, end = float(piece["from"]), float(piece["to"])
        for cue in read_cues(piece.get("srt")):
            if cue["end"] <= start or cue["start"] >= end:
                continue
            kept = min(cue["end"], end) - max(cue["start"], start)
            # A sentence cut by the edge of a piece leaves a sliver on the
            # other side. Two tenths of a second is a flicker, not a caption.
            if kept < SLIVER:
                continue
            merged.append({
                "start": max(cue["start"], start) - start + clock,
                "end": min(cue["end"], end) - start + clock,
                "text": cue["text"],
            })
        clock += end - start
    return merged


def merge_scene(pieces: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The pieces' layouts, moved onto the finished video's clock.

    Subtitles are not the only thing timed against the old clock: a card that
    appeared at 3:10 of an interview appears eleven seconds later once eleven
    seconds of footage are put in front of it. Carrying the layout over and
    leaving the numbers alone puts every card in the wrong place, which is
    worse than having no layout at all.

    The first piece that has a layout supplies the frame -- canvas, video box,
    subtitle style -- because those describe how the picture is arranged and
    not when anything happens. Timed elements come from every piece, trimmed to
    its edges the way captions are.
    """
    frame: dict[str, Any] | None = None
    timed: list[dict[str, Any]] = []
    clock = 0.0
    for piece in pieces:
        path = piece.get("scene")
        scene = (json.loads(Path(path).read_text(encoding="utf-8"))
                 if path and Path(path).is_file() else None)
        start, end = float(piece["from"]), float(piece["to"])
        if scene:
            elements = scene.get("elements", [])
            if frame is None:
                frame = {key: value for key, value in scene.items()
                         if key != "elements"}
                frame["elements"] = [dict(element) for element in elements
                                     if element.get("from") is None]
            for element in elements:
                if element.get("from") is None:
                    continue
                first, last = float(element["from"]), float(element.get("to", 0))
                if last <= start or first >= end:
                    continue
                timed.append({**element,
                              "from": round(max(first, start) - start + clock, 3),
                              "to": round(min(last, end) - start + clock, 3)})
        clock += end - start
    if frame is None:
        return None
    frame["elements"] = frame["elements"] + timed
    return frame


def write_srt(cues: list[dict[str, Any]], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [
        f"{index}\n{timestamp(cue['start'], True)} --> {timestamp(cue['end'], True)}\n"
        f"{cue['text'].strip()}"
        for index, cue in enumerate(cues, start=1)
        if cue["text"].strip()
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return path


def _graph(count: int) -> str:
    """Normalise every piece, then join. Sources differ in size, frame rate and
    sample rate; concat refuses to join streams that disagree, and a viewer
    would see the disagreement anyway."""
    parts = []
    for index in range(count):
        parts.append(
            f"[{index}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS}[v{index}]"
        )
        parts.append(f"[{index}:a]aresample={RATE},aformat=channel_layouts=stereo[a{index}]")
    streams = "".join(f"[v{index}][a{index}]" for index in range(count))
    parts.append(f"{streams}concat=n={count}:v=1:a=1[v][a]")
    return ";".join(parts)


def assemble(pieces: list[dict[str, Any]], video_out: Path,
             progress: Path | None = None) -> dict[str, Any]:
    """Join the pieces into one file. Returns where each piece landed."""
    if not pieces:
        raise ValueError("沒有片段可以組裝")

    command = ["ffmpeg", "-y", "-v", "error"]
    for piece in pieces:
        source = Path(piece["source"])
        if not source.is_file():
            raise FileNotFoundError(f"找不到 {source}")
        command.extend(["-ss", f"{float(piece['from']):.3f}",
                        "-to", f"{float(piece['to']):.3f}", "-i", str(source)])

    video_out.parent.mkdir(parents=True, exist_ok=True)
    if progress:
        command.extend(["-progress", str(progress), "-nostats"])
    command.extend([
        "-filter_complex", _graph(len(pieces)),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(video_out),
    ])
    done = subprocess.run(command, capture_output=True, text=True)
    if done.returncode:
        # The whole command line is not an error message. ffmpeg says what went
        # wrong on its last line; that is what belongs on screen.
        said = [line for line in (done.stderr or "").splitlines() if line.strip()]
        raise RuntimeError(said[-1] if said else f"ffmpeg 失敗（{done.returncode}）")

    clock, laid = 0.0, []
    for piece in pieces:
        span = float(piece["to"]) - float(piece["from"])
        # Paths are written back out as text: this record is saved as JSON so
        # the assembly can be opened and adjusted later.
        laid.append({**piece,
                     "source": str(piece["source"]),
                     "srt": str(piece["srt"]) if piece.get("srt") else None,
                     "at": round(clock, 3), "seconds": round(span, 3)})
        clock += span
    return {"file": str(video_out), "duration": round(clock, 3), "pieces": laid}
