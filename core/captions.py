"""Reading a video's subtitles, so the pictures taken from it are chosen
rather than sampled.

Frames used to be cut at evenly spaced moments -- three or four across the
running time, whatever happened to be on screen. What that gives you is the
titles, the anchor's face, and two people sitting on stools; a broadcast cuts
every few seconds and the useful shot is almost never at 1/4, 2/4, 3/4.

The subtitles say what is being talked about at each second, and a news cut
generally shows what it is talking about. So the caption track is the index:
find the line that mentions the thing, take the picture there.

The text is not proof -- the words may be spoken over a reporter's face -- so
what this returns is a candidate, to be looked at before it is used.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

STAMP = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")
TAGS = re.compile(r"<[^>]+>")


def _seconds(hours: str, minutes: str, secs: str, milli: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(milli) / 1000


def read(path: Path | str) -> list[dict[str, Any]]:
    """The cues of a VTT or SRT file, in order.

    YouTube's automatic captions repeat themselves: each cue carries the tail
    of the one before so the words appear to roll. Left alone that makes every
    search match three times over, so a cue keeps only what it adds.
    """
    path = Path(path)
    if not path.is_file():
        return []
    cues: list[dict[str, Any]] = []
    start = end = 0.0
    said: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        found = STAMP.search(raw)
        if found:
            if said:
                cues.append({"start": start, "end": end,
                             "text": " ".join(said).strip()})
            said = []
            start = _seconds(*found.group(1, 2, 3, 4))
            end = _seconds(*found.group(5, 6, 7, 8))
            continue
        line = TAGS.sub("", raw).strip()
        if line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if line and not line.isdigit():
            said.append(line)
    if said:
        cues.append({"start": start, "end": end, "text": " ".join(said).strip()})

    tidy: list[dict[str, Any]] = []
    for cue in cues:
        if not cue["text"]:
            continue
        if tidy and cue["text"] in tidy[-1]["text"]:
            tidy[-1]["end"] = cue["end"]      # a repeat, not a new line
            continue
        if tidy and tidy[-1]["text"] in cue["text"]:
            cue["text"] = cue["text"][len(tidy[-1]["text"]):].strip() or cue["text"]
        tidy.append(cue)
    return tidy


def moments(cues: list[dict[str, Any]], words: list[str],
            most: int = 6, apart: float = 12.0) -> list[dict[str, Any]]:
    """Where in this video the words are being said.

    Returns the middle of each matching cue rather than its start: a cut
    usually lands on the first syllable of the sentence that describes it, and
    the shot that illustrates it is a beat later.

    Matches are kept `apart` seconds from each other. Broadcast repeats its
    key phrase several times in one breath, and three frames from one sentence
    are three copies of one picture.
    """
    wanted = [word.lower() for word in words if word and len(word) > 2]
    if not wanted:
        return []
    found: list[dict[str, Any]] = []
    for cue in cues:
        text = cue["text"].lower()
        hits = [word for word in wanted if word in text]
        if not hits:
            continue
        middle = round((cue["start"] + cue["end"]) / 2, 2)
        if found and middle - found[-1]["at"] < apart:
            # the same breath: keep whichever cue matched more of the words
            if len(hits) > len(found[-1]["hits"]):
                found[-1] = {"at": middle, "said": cue["text"], "hits": hits}
            continue
        found.append({"at": middle, "said": cue["text"], "hits": hits})
    found.sort(key=lambda one: (-len(one["hits"]), one["at"]))
    return found[:most]


def passages(cues: list[dict[str, Any]], words: list[str],
             want: float = 5.0, most: int = 4) -> list[dict[str, Any]]:
    """Stretches worth cutting as clips rather than stills.

    A clip has to start and end on a sentence boundary or it sounds -- looks,
    here, since these run silent -- like a mistake. So a passage grows by whole
    cues out from the one that matched, until it is long enough.
    """
    hits = moments(cues, words, most=most * 2, apart=want * 2)
    out: list[dict[str, Any]] = []
    for hit in hits:
        index = next((i for i, cue in enumerate(cues)
                      if cue["start"] <= hit["at"] <= cue["end"]), None)
        if index is None:
            continue
        first = last = index
        while cues[last]["end"] - cues[first]["start"] < want:
            if last + 1 < len(cues):
                last += 1
            elif first > 0:
                first -= 1
            else:
                break
        span = cues[last]["end"] - cues[first]["start"]
        if span < want * 0.6:
            continue
        out.append({"start": round(cues[first]["start"], 2),
                    "end": round(cues[last]["end"], 2),
                    "seconds": round(span, 2),
                    "said": " ".join(cue["text"] for cue in cues[first:last + 1]),
                    "hits": hit["hits"]})
        if len(out) >= most:
            break
    return out
