"""Tidy caption boundaries without discarding what Whisper knew.

Whisper ends a caption where the speaker paused, so one line can read
還發生了兩處邊坡 and the next open with 坍方 -- a word split in two. The fix is
not to rebuild the captions from word timings: on Chinese only a seventh of
segments carry punctuation, and a segment boundary is itself how Whisph marks a
pause. Flattening the transcript throws away every one of those boundaries.

So the segments are kept and only adjusted: fragments that belong to one
sentence are joined, and a line too long to read is divided at a pause inside
it. Word timings are consulted for where a division falls in time, never to
reconstruct the text.
"""
from __future__ import annotations

import re
from typing import Any

MAX_CHARS = 22         # characters on one line before it reads as a wall
MAX_SECONDS = 6.0      # longer than this and the line outstays the speech
MIN_SECONDS = 0.9      # shorter than this and it flashes past
MAX_CPS = 9.0          # characters per second a viewer can follow
JOIN_GAP = 0.5         # silence beyond this is a real pause, not a split word

SENTENCE_MARKS = "。！？.!?"
CLAUSE_MARKS = "，、；：,;:"
ALL_MARKS = SENTENCE_MARKS + CLAUSE_MARKS


def _visible(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _ends_sentence(text: str) -> bool:
    return bool(text) and text.rstrip()[-1:] in SENTENCE_MARKS


def _joined(first: str, second: str) -> str:
    """Join two fragments, marking the pause that used to separate them.

    The boundary was Whisper reporting a pause. Where the halves are clauses of
    one sentence a comma preserves that; where the first already ends in
    punctuation nothing needs adding.
    """
    left, right = first.rstrip(), second.lstrip()
    if not left:
        return right
    if left[-1] in ALL_MARKS:
        return f"{left}{right}"
    return f"{left}，{right}"


def join_fragments(
    segments: list[dict[str, Any]],
    *,
    max_chars: int = MAX_CHARS,
    max_seconds: float = MAX_SECONDS,
) -> list[dict[str, Any]]:
    """Merge consecutive captions that read as one sentence."""
    merged: list[dict[str, Any]] = []
    for segment in segments:
        if not merged:
            merged.append(dict(segment))
            continue
        previous = merged[-1]
        candidate = _joined(previous["text"], segment["text"])
        adjacent = segment["start"] - previous["end"] <= JOIN_GAP
        fits = (_visible(candidate) <= max_chars
                and segment["end"] - previous["start"] <= max_seconds)
        # A caption that already ended a sentence is complete; joining it to the
        # next would run two sentences together on one line.
        if adjacent and fits and not _ends_sentence(previous["text"]):
            previous["text"] = candidate
            previous["end"] = segment["end"]
            previous["logprob"] = min(previous.get("logprob", 0.0),
                                      segment.get("logprob", 0.0))
            if segment.get("origin") == "gap":
                previous["origin"] = "gap"
        else:
            merged.append(dict(segment))
    return merged


def _split_points(text: str) -> list[int]:
    """Offsets a caption may be divided at: after punctuation, or at a space.

    Nothing else qualifies. Chinese has no spaces between words, so any other
    offset risks cutting a word in half -- the very fault being repaired.
    """
    points = []
    for index, char in enumerate(text):
        if char in ALL_MARKS:
            points.append(index + 1)
        elif char.isspace() and index:
            points.append(index)
    return points


def _time_at(offset: int, text: str, start: float, end: float,
             words: list[dict[str, Any]] | None) -> float:
    """When the character at `offset` is spoken.

    Word timings give this directly when available; without them the caption's
    own span is divided by how far into the text the offset falls.
    """
    if words:
        spoken = 0
        for word in words:
            piece = _visible(str(word.get("word", "")))
            if spoken + piece > _visible(text[:offset]):
                return float(word["start"])
            spoken += piece
    share = _visible(text[:offset]) / max(1, _visible(text))
    return start + (end - start) * share


def split_long(
    segments: list[dict[str, Any]],
    words: list[dict[str, Any]] | None = None,
    *,
    max_chars: int = MAX_CHARS,
    max_seconds: float = MAX_SECONDS,
) -> list[dict[str, Any]]:
    """Divide captions too long to read, but only at a pause inside them."""
    out: list[dict[str, Any]] = []
    for segment in segments:
        text = segment["text"].strip()
        too_long = _visible(text) > max_chars or segment["end"] - segment["start"] > max_seconds
        points = _split_points(text) if too_long else []
        # Aim for the middle: a division there leaves two readable halves.
        usable = [p for p in points if 2 < p < len(text) - 2]
        if not usable:
            out.append({**segment, "text": text})
            continue

        target = len(text) / 2
        cut = min(usable, key=lambda p: abs(p - target))
        at = _time_at(cut, text, segment["start"], segment["end"],
                      [w for w in (words or [])
                       if segment["start"] - 0.05 <= w["start"] < segment["end"] + 0.05])
        at = min(max(at, segment["start"] + 0.3), segment["end"] - 0.3)
        head = {**segment, "text": text[:cut].strip(), "end": round(at, 2)}
        tail = {**segment, "text": text[cut:].strip(), "start": round(at, 2)}
        # Either half may still be too long, so feed them back through.
        out.extend(split_long([head, tail], words,
                              max_chars=max_chars, max_seconds=max_seconds)
                   if _visible(head["text"]) > max_chars or _visible(tail["text"]) > max_chars
                   else [head, tail])
    return out


def pace(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give a caption enough time to be read, where the next one allows it."""
    paced = [dict(item) for item in segments]
    for index, segment in enumerate(paced):
        needed = max(MIN_SECONDS, _visible(segment["text"]) / MAX_CPS)
        latest = paced[index + 1]["start"] if index + 1 < len(paced) else None
        wanted = segment["start"] + needed
        segment["end"] = round(min(wanted, latest) if latest else wanted, 2) \
            if wanted > segment["end"] else segment["end"]
        if segment["end"] <= segment["start"]:
            segment["end"] = round(segment["start"] + 0.3, 2)
    return paced


def tidy(
    segments: list[dict[str, Any]], words: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Join fragments, divide over-long lines, then give each room to be read."""
    result = pace(split_long(join_fragments(segments), words))
    return [{**item, "id": index} for index, item in enumerate(result, start=1)]


def describe(segments: list[dict[str, Any]]) -> str:
    if not segments:
        return "沒有字幕"
    spans = [s["end"] - s["start"] for s in segments]
    over = sum(1 for s in spans if s > MAX_SECONDS)
    fast = sum(1 for s in segments
               if _visible(s["text"]) / max(0.1, s["end"] - s["start"]) > MAX_CPS)
    wide = sum(1 for s in segments if _visible(s["text"]) > MAX_CHARS)
    return (f"{len(segments)} 段｜平均 {sum(spans)/len(spans):.1f}s"
            f"｜過長 {over}｜過快 {fast}｜字太多 {wide}")
