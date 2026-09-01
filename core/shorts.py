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

from core import rules as rules_module

WIDTH = rules_module.look("frame.width", 1080)
HEIGHT = rules_module.look("frame.height", 1920)
FPS = rules_module.look("frame.fps", 30)
LIMIT = 180.0            # YouTube's ceiling for a Short
SWEET = (25.0, 60.0)     # what actually holds attention
PICTURE_TOP = rules_module.look("frame.picture_top", 430)


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


# A still that pushes in slowly reads as film rather than as a slide. The
# push crops the edges away, so nothing may sit closer to them than this.
PUSH = rules_module.look("frame.push", 1.085)
SAFE = round(HEIGHT * (1 - 1 / PUSH) / 2) + 24


def _push_filter(seconds: float, size: tuple[int, int] | None = None) -> str:
    """Slowly enlarge a still, from its centre.

    zoompan starts at the top left corner unless told otherwise, so a still
    without x and y does not zoom -- it drifts down and to the right, and
    whatever was near the bottom leaves the frame. Naming the centre is the
    whole fix, and it is easy to forget because the first second looks fine.

    `size` is the frame the push comes out at, and leaving it to default is
    the second easy mistake: this was written for full-frame cards, so it
    returned 1080x1920 whatever went in. A landscape photograph put through it
    came out as a tall crop, and then being placed at PICTURE_TOP hung it 430
    pixels past the bottom of the film -- the picture ran off the end of the
    frame and only a strip of the blurred ground was left showing at the top.
    A photograph has to be pushed at its own shape.
    """
    wide, high = size or (WIDTH, HEIGHT)
    frames = round(seconds * FPS)
    return (f"fps={FPS},scale={wide * 2}:{high * 2},"
            f"zoompan=z='min(1+{(PUSH - 1) / frames:.6f}*on,{PUSH})'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={wide}x{high}:fps={FPS},fade=in:0:8")


def interlude(cards: list[Path], target: Path, each: float = 5.0) -> Path:
    """A silent break between passages: fetched charts, our words, our frame."""
    target.parent.mkdir(parents=True, exist_ok=True)
    work = target.parent / ".shorts"
    work.mkdir(parents=True, exist_ok=True)
    made = []
    for index, card in enumerate(cards):
        piece = work / f"beat{index}.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-loop", "1", "-i", str(card),
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", f"{each:.2f}", "-vf", _push_filter(each),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-shortest", str(piece), "-y",
        ], check=True)
        made.append(piece)
    listing = work / "beats.txt"
    listing.write_text("".join(f"file '{piece}'\n" for piece in made), encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-c", "copy", str(target), "-y"], check=True)
    return target


def clip_cut(video: Path, start: float, end: float, seconds: float,
             target: Path, overlay: Path | None = None,
             credit: str = "") -> Path:
    """One moving shot, cut to length, with its sound, in the tall frame.

    This is what was missing. A short built only from cards and stills has
    nothing on screen that moves, and without narration that is the whole of
    the medium given away: a photograph of a crowd cannot say that something
    is happening. Five videos were being downloaded per topic and used only as
    a place to take screenshots from.

    It used to be silent on purpose: Content ID matches the audio, and these
    run without narration anyway. That call has been reversed -- a news clip
    with the sound stripped is a moving photograph, and the one thing it had
    to offer over a still was that somebody is speaking in it.

    Keeping it costs three things silence did not:

    - the source may have no audio track at all, and mapping one that is not
      there fails the whole render
    - every outlet masters at a different level, so cuts between them jump.
      One loudness target, from rules.json
    - the picture is slowed when the passage is shorter than the line, and
      audio that is not slowed with it drifts out of sync within a second

    `overlay` is the line's caption drawn by the same code that draws it on a
    still, so the type does not change when the picture starts moving. The
    broadcaster's name goes on our frame, above the picture, where no crop can
    take it off.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    span = max(0.1, end - start)
    # A clip held longer than it lasts freezes on its last frame. Slow it
    # instead: a line needs its own duration, and a shot that stops moving
    # halfway through is worse than one that moves a little too slowly.
    rate = min(1.0, span / seconds) if seconds > 0 else 1.0
    steps = [f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
             f"crop={WIDTH}:{HEIGHT},boxblur=44:3,eq=brightness=-0.14[bg]",
             f"[0:v]scale={WIDTH}:-2,setpts={1 / rate:.4f}*PTS[fg]",
             f"[bg][fg]overlay=(W-w)/2:{PICTURE_TOP}[under]"]
    last = "under"
    if credit:
        steps.append(f"[{last}]{_credit_filter(credit)[1:]}[marked]")
        last = "marked"
    if overlay:
        steps.append(f"[{last}][1:v]overlay=0:0[out]")
        last = "out"
    steps.append(f"[{last}]fps={FPS},trim=duration={seconds:.2f},"
                 f"setpts=PTS-STARTPTS,fade=in:0:6[v]")

    # The sound. `anullsrc` stays as the input either way, because a passage
    # whose source has no audio track still has to come out with one: the film
    # is joined with `-c copy`, and a piece missing a stream drops every
    # stream after it.
    silence = 2 if overlay else 1
    if _has_sound(video):
        steps.append(f"[0:a]{_sound_filter(rate, seconds)}[a]")
        sound = "[a]"
    else:
        sound = f"{silence}:a"
    command = ["ffmpeg", "-v", "error",
               "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(video)]
    if overlay:
        command += ["-i", str(overlay)]
    command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-filter_complex", ";".join(steps),
                "-map", "[v]", "-map", sound,
                "-t", f"{seconds:.2f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-pix_fmt", "yuv420p",
                # Same shape as the silent track the cards and stills carry,
                # or the join produces a file whose sound stops partway.
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                "-shortest", str(target), "-y"]
    subprocess.run(command, check=True)
    return target


def _has_sound(video: Path) -> bool:
    """Whether the file carries an audio stream.

    Asked rather than assumed. Footage arrives from several places -- yt-dlp
    with opus, stock libraries with none at all -- and `-map 0:a` on a file
    without one fails the render with a stream-not-found, at the point where
    thirty other shots have already been encoded.
    """
    try:
        got = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0",
             str(video)], capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return bool(got.stdout.strip())


def _sound_filter(rate: float, seconds: float) -> str:
    """Stretch, level and fade one passage's audio.

    `rate` is the same number the picture is slowed by, so the two stay
    together. `atempo` only takes 0.5 to 2.0, so a passage stretched further
    than double needs the factor split across several -- a five-second line
    holding a two-second passage is 0.4, which one `atempo` refuses and two
    accept. It does sound like slow motion. So does the picture.

    Loudness is levelled in one pass. Two-pass measurement is more accurate
    and would mean probing every passage before rendering any of them, which
    is the kind of step that gets skipped.
    """
    from . import rules as rules_module
    want = rules_module.at("sound.clip_loudness", -16)
    peak = rules_module.at("sound.clip_peak", -1.5)
    fade = rules_module.at("sound.fade", 0.15)

    steps = []
    left = max(0.05, min(1.0, rate))
    while left < 0.5 - 1e-9:                    # 0.5 是 atempo 的下限
        steps.append("atempo=0.5")
        left /= 0.5
    if abs(left - 1.0) > 1e-6:
        steps.append(f"atempo={left:.6f}")
    steps.append("aresample=48000")
    # loudnorm 要大約三秒才算得出東西。給它 1.9 秒，它回 NaN，然後 aac 拒收
    # 整個鏡頭 ——「Input contains (near) NaN/+-Inf」，壓片直接失敗。
    #
    # 短的用 dynaudnorm：它是逐格算的，短輸入不會爆，代價是它比較不準。
    # 不準沒關係，NaN 有關係 —— 一個兩秒的鏡頭響度差三分貝沒有人聽得出來，
    # 而壓不出來是所有人都看得到。
    if seconds >= 3.0:
        steps.append(f"loudnorm=I={want}:TP={peak}:LRA=11")
    else:
        steps.append("dynaudnorm=f=100:g=5")
    # **這一行才是真的在擋。** loudnorm 對著一段幾乎全是零的樣本算增益，
    # 算出來是 NaN；aac 收到 NaN 就整支拒收，壓片直接失敗 ——
    # 「Input contains (near) NaN/+-Inf」。alimiter 把它夾回有限值。
    #
    # 上面那個「短的改用 dynaudnorm」是第二層：它讓短鏡頭的響度算得比較
    # 合理，但就算拿掉，有 alimiter 就不會爆。分清楚哪一層在擋什麼很重要
    # —— 我第一次種錯種在長度那一層，門說通過，而我差點以為門壞了。
    steps.append(f"alimiter=limit={10 ** (peak / 20):.4f}")
    steps.append("asetpts=PTS-STARTPTS")
    # 淡入淡出。沒有它，每一段的頭尾是啪的一聲 —— 而那個聲音在有人戴耳機
    # 看的時候最明顯，也就是這種片子絕大多數被看的方式。
    out = max(0.0, seconds - fade)
    steps.append(f"afade=t=in:st=0:d={fade}")
    steps.append(f"afade=t=out:st={out:.2f}:d={fade}")
    steps.append(f"apad,atrim=duration={seconds:.2f}")
    return ",".join(steps)


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
