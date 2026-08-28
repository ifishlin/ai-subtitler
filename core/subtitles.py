"""Writing what the run produced: a transcript, and the SRT files to burn.

Which files appear depends on what exists. A translated run writes the spoken
language, the Chinese, and a bilingual file with both; an untranslated one
writes only what was said. The caller picks from what came back rather than
guessing at names.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import timestamp

def _write_srt(path: Path, segments: list[dict[str, Any]], render_text) -> None:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        text = render_text(segment).strip()
        if text:
            blocks.append(
                f"{len(blocks) + 1}\n"
                f"{timestamp(segment['start'], True)} --> {timestamp(segment['end'], True)}\n{text}"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def save_transcript(
    segments: list[dict[str, Any]], txt_path: Path, srt_path: Path
) -> dict[str, Path]:
    """Write the transcript and every subtitle file the segments support.

    A translated run yields three: the spoken language on its own, the Chinese
    on its own, and a bilingual file with Chinese above the original. Plain
    files stay usable elsewhere, so no styling tags are embedded in them.
    """
    txt_path.write_text(
        "\n".join(f"[{timestamp(s['start'])} --> {timestamp(s['end'])}] {s['text']}" for s in segments) + "\n",
        encoding="utf-8",
    )
    written = {}
    if any(item.get("zh") for item in segments):
        _write_srt(srt_path.with_name("subtitles_source.srt"), segments, lambda s: s["text"])
        _write_srt(srt_path, segments, lambda s: s.get("zh", ""))
        bilingual = srt_path.with_name("subtitles_bilingual.srt")
        _write_srt(bilingual, segments,
                   lambda s: f"{s['zh']}\n{s['text']}" if s.get("zh") else s["text"])
        written = {"source": srt_path.with_name("subtitles_source.srt"),
                   "zh": srt_path, "bilingual": bilingual}
    else:
        _write_srt(srt_path, segments, lambda s: s["text"])
        written = {"zh": srt_path}
    return written


def merge_extra_segments(
    segments: list[dict[str, Any]], sidecar_path: Path
) -> list[dict[str, Any]]:
    """Merge reviewed captions for speech that Whisper missed (e.g. street interviews)."""
    if not sidecar_path.is_file():
        return segments
    extra = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if not isinstance(extra, list):
        raise ValueError(f"Expected a JSON list in {sidecar_path}")
    merged = [{**item} for item in segments]
    for item in extra:
        start, end = float(item["start"]), float(item["end"])
        text = str(item["text"]).strip()
        if text and end > start:
            merged.append({"id": 0, "start": start, "end": end, "text": text})
    merged.sort(key=lambda item: (item["start"], item["end"]))
    return [{**item, "id": index} for index, item in enumerate(merged, start=1)]
