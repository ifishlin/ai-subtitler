"""Cutting a vertical short out of a finished run.

A short is not a small version of the video; it is one passage of it, chosen
because it survives being taken out. Three things decide whether it works, and
only the first is a matter of taste:

  which passage    a judgement, made from the transcript
  where to crop    a measurement, made from the frames
  what to say      the captions, which the run already has

The crop is the part people get wrong. A face-tracking crop is the obvious
answer and is usually the wrong one: in a news explainer the subject is a
graphic on a video wall, and following the presenter's face crops the numbers
off the screen. Nor can the right crop be calculated -- see contact_sheet for
two attempts and how each failed. It is recognised, by an eye, from a sheet of
sampled frames, and then remembered: a studio frames its wall the same way
every night, so the crop belongs to the programme rather than to the clip.
Where nothing has been recognised, the whole picture is kept over a blurred
enlargement of itself, which is never wrong and only ever plain.

    9:16, 1080x1920, up to three minutes -- what YouTube calls a Short.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WIDTH, HEIGHT, FPS = 1080, 1920, 30
LIMIT = 180.0            # YouTube's ceiling for a Short
SWEET = (25.0, 60.0)     # what actually holds attention
PICTURE_TOP = 430        # where the kept picture sits on the tall canvas


@dataclass
class Passage:
    """A stretch of the source worth taking out on its own."""
    start: float
    end: float
    lines: list[dict[str, Any]] = field(default_factory=list)
    why: str = ""

    @property
    def seconds(self) -> float:
        return self.end - self.start


def _probe(video: Path) -> dict[str, Any]:
    said = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-show_entries", "format=duration",
         "-of", "json", str(video)], capture_output=True, text=True).stdout
    found = json.loads(said)
    stream = (found.get("streams") or [{}])[0]
    return {"width": int(stream.get("width") or 1920),
            "height": int(stream.get("height") or 1080),
            "seconds": float(found.get("format", {}).get("duration") or 0.0)}


def tidy_edges(passage: Passage, cues: list[dict[str, Any]],
               lead: float = 0.3) -> Passage:
    """Move the cut to the nearest caption edge, and take a run-up.

    Recognition timings sit a little late, so a cut placed exactly on the first
    caption clips the first syllable. A third of a second before it costs
    nothing and never does.
    """
    inside = [cue for cue in cues
              if cue["end"] > passage.start and cue["start"] < passage.end]
    if not inside:
        return passage
    return Passage(start=max(0.0, inside[0]["start"] - lead),
                   end=inside[-1]["end"], lines=inside, why=passage.why)


# ------------------------------------------------------------------ framing

def _sample(video: Path, at: float, into: Path) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.2f}", "-i", str(video),
                    "-frames:v", "1", str(into), "-y"], check=True)
    return into


def contact_sheet(video: Path, passage: Passage, work: Path,
                  samples: int = 6, across: int = 3) -> Path:
    """Every few seconds of the passage as one picture, to be looked at.

    This is the input to choosing a crop, and looking is the method. Two
    arithmetic shortcuts were tried on a news explainer and both failed, in
    opposite directions:

        brightness      chose the whole studio -- all of it is lit
        edge density    chose the broadcaster's lower-third banner -- white
                        text on white is the sharpest thing in the frame, and
                        it is exactly what has to go

    What a person sees instead is "a screen with the numbers on it", which is
    recognition, not measurement. So the sheet is made for an eye -- a person's
    or a model's -- and the crop comes back as a decision, not a calculation.
    """
    from PIL import Image

    work.mkdir(parents=True, exist_ok=True)
    span = passage.seconds
    shots = [_sample(video, passage.start + span * (i + 0.5) / samples,
                     work / f"probe{i:02d}.png") for i in range(samples)]
    with Image.open(shots[0]) as first:
        wide, tall = first.size
    cell = (480, round(480 * tall / wide))
    down = (samples + across - 1) // across
    sheet = Image.new("RGB", (cell[0] * across, cell[1] * down), "#101418")
    for index, path in enumerate(shots):
        with Image.open(path) as frame:
            sheet.paste(frame.resize(cell),
                        ((index % across) * cell[0], (index // across) * cell[1]))
    target = work / "contact.jpg"
    sheet.save(target, quality=88)
    return target


# A crop belongs to a programme, not to a clip: a studio frames its wall the
# same way every night, so it is measured once and named, like a house style.
CROPS = Path(__file__).resolve().parent.parent / "assets" / "crops.json"


def crops() -> dict[str, list[int]]:
    if CROPS.is_file():
        return json.loads(CROPS.read_text(encoding="utf-8"))
    return {}


def remember_crop(name: str, box: tuple[int, int, int, int]) -> None:
    kept = crops()
    kept[name] = [int(value) & ~1 for value in box]     # odd sizes upset encoders
    CROPS.parent.mkdir(parents=True, exist_ok=True)
    CROPS.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")


def crop_for(name: str | None) -> tuple[int, int, int, int] | None:
    """The crop for this programme, or None -- keep the whole picture.

    None is the honest default. Keeping everything over a blurred enlargement
    of itself is never wrong and only ever plain; a wrong crop cuts the story
    out of the frame.
    """
    box = crops().get(name or "")
    return tuple(box) if box and len(box) == 4 else None


def _credit_filter(credit: str) -> str:
    """Whose footage this is, on screen for the whole short.

    A crop that makes a graphic readable also cuts off the broadcaster's logo
    and lower-third -- which is most of the frame's attribution. Taking someone
    else's pictures, removing their name and adding your own commentary is
    misappropriation whatever the intent, so the name goes back on, in our own
    frame, where no crop can remove it. On the short throughout, not only on
    the card at the end: most people never reach the end.
    """
    safe = credit.replace("\\", "\\\\").replace(":", "\\:").replace("'", "")
    return (f",drawtext=text='{safe}':fontfile=/System/Library/Fonts/PingFang.ttc"
            f":fontsize=34:fontcolor=white@0.92:box=1:boxcolor=black@0.45"
            f":boxborderw=14:x=(w-text_w)/2:y={PICTURE_TOP - 62}")


def _graph(box: tuple[int, int, int, int] | None, subtitles: Path | None,
           credit: str = "") -> str:
    """Blurred enlargement behind, the kept picture in front."""
    background = (f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                  f"crop={WIDTH}:{HEIGHT},boxblur=44:3,eq=brightness=-0.14[bg]")
    if box:
        x, y, w, h = box
        front = f"[0:v]crop={w}:{h}:{x}:{y},scale={WIDTH}:-2[fg]"
    else:
        front = f"[0:v]scale={WIDTH}:-2[fg]"
    burn = (f",subtitles='{subtitles}':fontsdir=/System/Library/Fonts"
            if subtitles else "")
    mark = _credit_filter(credit) if credit else ""
    return (f"{background};{front};"
            f"[bg][fg]overlay=(W-w)/2:{PICTURE_TOP}{mark}{burn},fps={FPS}[v]")


def render(video: Path, passage: Passage, target: Path,
           subtitles: Path | None = None, box: tuple[int, int, int, int] | None = None,
           card: Path | None = None, card_seconds: float = 10.0,
           credit: str = "") -> dict[str, Any]:
    """Cut the passage, stand it in a vertical frame, and add the comment.

    `credit` names whose footage this is and is not optional in practice: a
    crop tight enough to be readable removes the broadcaster's own marks.
    """
    if box and not credit:
        raise ValueError("裁切會把台標切掉，必須用 credit 指明畫面來源")
    target.parent.mkdir(parents=True, exist_ok=True)
    work = target.parent / ".shorts"
    work.mkdir(parents=True, exist_ok=True)
    body = work / "body.mp4"

    subprocess.run([
        "ffmpeg", "-v", "error",
        "-ss", f"{passage.start:.3f}", "-to", f"{passage.end:.3f}", "-i", str(video),
        "-filter_complex", _graph(box, subtitles, credit),
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", str(body), "-y",
    ], check=True)

    if not card:
        body.replace(target)
        return {"file": str(target), "seconds": round(passage.seconds, 2),
                "cropped": bool(box)}

    tail = work / "card.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-loop", "1", "-i", str(card),
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{card_seconds:.2f}", "-vf", f"fps={FPS},fade=in:0:12",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-shortest", str(tail), "-y",
    ], check=True)

    listing = work / "join.txt"
    listing.write_text(f"file '{body}'\nfile '{tail}'\n", encoding="utf-8")
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c", "copy", "-movflags", "+faststart", str(target), "-y",
    ], check=True)
    return {"file": str(target), "cropped": bool(box),
            "seconds": round(passage.seconds + card_seconds, 2)}
