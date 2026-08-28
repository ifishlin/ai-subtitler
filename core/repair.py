"""Fix what the audit can measure, so a person only sees what needs judgement.

A caption running at 58 characters a second is not a matter of opinion: it is
too fast, and either it wants more time or it wants dividing. Reporting that
and waiting for someone is the part that does not scale -- most of an audit's
findings are arithmetic, and arithmetic should be done by the program.

Three repairs, in the order that does the least damage:

    overlaps    pull a caption's end back off the next one's start
    too fast    give it the time that is free before the next caption
    too long    divide it at a pause inside it, translation and all

Nothing invents text and nothing reorders. A repair that cannot be made
cleanly is left alone for the review that follows, which is the point: what
survives this pass is what actually needed a reader.
"""
from __future__ import annotations

import re
from typing import Any

from .segment import (
    ALL_MARKS,
    MIN_SECONDS,
    SENTENCE_MARKS,
    _visible,
    limits_for,
)

MAX_SECONDS = 6.0
CPS_MARGIN = 1.6        # matches audit's: only well past readable is a fault
GAP_KEEP = 0.04         # leave a frame between neighbours after moving an end
MIN_HALF = 0.35         # a division must leave both halves visible


def _split_points(text: str) -> list[int]:
    """Offsets just after punctuation, preferring the end of a sentence."""
    strong = [m.end() for m in re.finditer(f"[{re.escape(SENTENCE_MARKS)}]", text)]
    weak = [m.end() for m in re.finditer(f"[{re.escape(ALL_MARKS)}]", text)]
    spaces = [m.start() for m in re.finditer(r"\s", text)]
    for candidates in (strong, weak, spaces):
        usable = [p for p in candidates if 2 < p < len(text) - 2]
        if usable:
            return usable
    return []


def _divide(segment: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Two captions from one, cut at a pause. None when there is no clean cut."""
    text = str(segment.get("text", "")).strip()
    points = _split_points(text)
    if not points:
        return None
    span = segment["end"] - segment["start"]
    if span < MIN_HALF * 2:
        return None

    cut = min(points, key=lambda p: abs(p - len(text) / 2))
    head_text, tail_text = text[:cut].strip(), text[cut:].strip()
    if not head_text or not tail_text:
        return None

    # Time is divided the way the text is, which is the best guess available
    # once word timings are long gone -- and it is only ever a guess about
    # where inside its own span a caption changes.
    share = len(head_text) / max(1, len(head_text) + len(tail_text))
    at = segment["start"] + span * share
    at = min(max(at, segment["start"] + MIN_HALF), segment["end"] - MIN_HALF)

    head = {**segment, "text": head_text, "end": round(at, 2)}
    tail = {**segment, "text": tail_text, "start": round(at, 2)}

    # A bilingual caption carries its translation, which has to be divided too
    # or the halves would both claim the whole sentence.
    if segment.get("zh"):
        from .translate import _split_translation
        parts = _split_translation(str(segment["zh"]), [head, tail])
        head["zh"], tail["zh"] = parts[0], parts[1] if len(parts) > 1 else ""
    return [head, tail]


def repair(
    segments: list[dict[str, Any]],
    duration: float | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return the mended captions and a line per repair made."""
    if not segments:
        return segments, []

    limits = limits_for(segments)
    max_chars, max_cps = int(limits["chars"]), limits["cps"]
    notes: list[str] = []
    work = [dict(item) for item in sorted(segments, key=lambda s: (s["start"], s["end"]))]

    # 1. Overlaps first: everything below reasons about the room between
    #    captions, and an overlap means that room is negative.
    for earlier, later in zip(work, work[1:]):
        if later["start"] < earlier["end"] - 0.05:
            earlier["end"] = round(max(earlier["start"] + MIN_HALF,
                                       later["start"] - GAP_KEEP), 2)
            notes.append(f"{earlier['start']:.1f}s 收回與下一段重疊的時間")

    # 2. Too fast, or gone in a flash: take the silence that follows, if any.
    for index, segment in enumerate(work):
        chars = _visible(str(segment.get("text", "")))
        span = segment["end"] - segment["start"]
        if span <= 0:
            continue
        needed = max(MIN_SECONDS, chars / max_cps)
        crowded = chars / span > max_cps * CPS_MARGIN
        if not crowded and span >= MIN_SECONDS:
            continue
        ceiling = work[index + 1]["start"] - GAP_KEEP if index + 1 < len(work) else (
            duration if duration else segment["end"])
        wanted = min(segment["start"] + needed, ceiling)
        if wanted > segment["end"] + 0.05:
            was = span
            segment["end"] = round(wanted, 2)
            notes.append(f"{segment['start']:.1f}s 由 {was:.1f} 秒延長到 "
                         f"{segment['end'] - segment['start']:.1f} 秒")

    # 3. Too much text on screen at once, or on screen too long: divide.
    #
    #    Note what is NOT here. Dividing cannot mend a caption that reads too
    #    fast: characters and seconds are split in the same proportion, so
    #    characters per second comes out identical. Trying it turned 19
    #    findings into 28 -- each half kept the pace and several fell under the
    #    flash threshold. Speech that outruns reading is a fact about the
    #    speaking, and the only honest repairs are more time or none.
    divided: list[dict[str, Any]] = []
    for segment in work:
        chars = _visible(str(segment.get("text", "")))
        span = segment["end"] - segment["start"]
        if chars <= max_chars and span <= MAX_SECONDS:
            divided.append(segment)
            continue
        halves = _divide(segment)
        # Two captions that each flash past are worse than one that is long.
        if not halves or any(h["end"] - h["start"] < MIN_SECONDS for h in halves):
            divided.append(segment)
            continue
        divided.extend(halves)
        notes.append(f"{segment['start']:.1f}s 切成兩段：{halves[0]['text'][:16]}｜"
                     f"{halves[1]['text'][:16]}")

    # Ids number the captions in order, and dividing one moves every id after
    # it. Sidecars keyed by id are applied long before this runs.
    return [{**item, "id": index} for index, item in enumerate(divided, start=1)], notes
