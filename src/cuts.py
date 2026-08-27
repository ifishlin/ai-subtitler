"""Taking stretches out of a video, without touching the video.

A cut is a range of the source that should not appear. Nothing is deleted:
the cuts are a list, kept beside the layout, and the burn honours them. That
is what makes them reversible, and it keeps the subtitle numbering stable --
line 84 stays line 84 whether or not the minute before it survives.

The work is not the cutting. It is that everything after a cut moves: remove
ten seconds at a minute in and every caption, every placed image, every card
after that point happens ten seconds earlier. So the module's real job is one
mapping, from source time to finished time, applied to everything with a clock
on it.
"""
from __future__ import annotations

from typing import Any

Range = tuple[float, float]


def tidy(cuts: list[Any]) -> list[Range]:
    """Sorted, non-overlapping, nothing backwards. Two cuts that touch become
    one, so the arithmetic below never has to think about the seam."""
    ranges = []
    for cut in cuts or []:
        start, end = (float(cut[0]), float(cut[1])) if isinstance(cut, (list, tuple)) \
            else (float(cut["start"]), float(cut["end"]))
        if end > start:
            ranges.append((start, end))
    ranges.sort()

    merged: list[Range] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def removed_before(cuts: list[Range], moment: float) -> float:
    """How much of the source has been dropped before this point."""
    total = 0.0
    for start, end in cuts:
        if end <= moment:
            total += end - start
        elif start < moment:
            total += moment - start          # inside a cut: only what precedes
    return total


def inside(cuts: list[Range], moment: float) -> Range | None:
    for start, end in cuts:
        if start <= moment < end:
            return (start, end)
    return None


def remap(cuts: list[Range], moment: float) -> float:
    """Source time to finished time. A moment inside a cut lands on the seam,
    which is where the viewer would be at that instant."""
    return max(0.0, moment - removed_before(cuts, moment))


def duration_after(cuts: list[Range], duration: float) -> float:
    return max(0.0, duration - sum(min(end, duration) - start
                                   for start, end in cuts if start < duration))


def survives(cuts: list[Range], start: float, end: float) -> tuple[float, float] | None:
    """A timed thing's new window, or None if the cut swallowed it whole.

    Something straddling a cut keeps its head and its tail, which now meet.
    Something wholly inside one is gone -- there is no moment left to show it."""
    if end <= start:
        return None
    kept = (end - start) - sum(max(0.0, min(end, cut_end) - max(start, cut_start))
                               for cut_start, cut_end in cuts)
    if kept <= 0.01:
        return None
    new_start = remap(cuts, start)
    return (new_start, new_start + kept)


def apply_to_scene(scene: dict[str, Any], cuts: list[Range]) -> dict[str, Any]:
    """The scene as the finished video sees it: elements whose moment was cut
    are dropped, the rest move onto the new clock."""
    if not cuts:
        return scene
    kept = []
    for element in scene.get("elements", []):
        if element.get("from") is None:
            kept.append(element)
            continue
        window = survives(cuts, float(element["from"]),
                          float(element.get("to", element["from"])))
        if window is None:
            continue
        kept.append({**element, "from": round(window[0], 3), "to": round(window[1], 3)})
    return {**scene, "elements": kept}


def apply_to_cues(cues: list[dict[str, Any]], cuts: list[Range]) -> list[dict[str, Any]]:
    """The same, for anything with start and end -- captions, cards, chapters."""
    if not cuts:
        return cues
    moved = []
    for cue in cues:
        window = survives(cuts, float(cue["start"]), float(cue["end"]))
        if window is None:
            continue
        moved.append({**cue, "start": round(window[0], 3), "end": round(window[1], 3)})
    return moved


def filters(cuts: list[Range], duration: float | None = None) -> tuple[str, str]:
    """The ffmpeg select expressions that drop the cut frames and close the gap.

    Frames are chosen rather than the video being trimmed and concatenated:
    one pass, one re-encode, and no seam artefacts from stitching files. The
    timestamps have to be rebuilt afterwards or the player would sit through
    the removed stretch."""
    if not cuts:
        return "", ""
    # Without a duration the tail runs a day past anything real, which
    # saves the caller a probe and costs nothing: frames are selected by time,
    # and the stream ends when it ends.
    end_of_it = duration if duration is not None else max(end for _, end in cuts) + 86400
    keep = []
    clock = 0.0
    for start, end in cuts:
        if start > clock:
            keep.append((clock, start))
        clock = end
    if clock < end_of_it:
        keep.append((clock, end_of_it))

    windows = "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in keep)
    return (f"select='{windows}',setpts=N/FRAME_RATE/TB",
            f"aselect='{windows}',asetpts=N/SR/TB")
