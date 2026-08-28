"""Review state: the editable model assembled from read-only pipeline output.

Three files are combined:
  subtitles_zh.srt      the subtitles as shipped   -> editable text
  transcript.json       proofread text             -> "qwen" reference
  transcript_raw.json   raw Whisper text           -> "whisper" reference

transcript_raw.json is matched by time overlap rather than segment id, because
merge_extra_segments() renumbers ids once the reviewed sidecars are merged.
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from .srt import parse_srt, write_srt

MIN_GAP = 1.2          # seconds of silence before a gap is worth reviewing
RISK_THRESHOLD = 0.25  # how much the LLM rewrote Whisper before we flag it


def _load_segments(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("segments", []) if isinstance(data, dict) else data


def _best_overlap(segment: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the candidate sharing the most time with segment, if it overlaps enough."""
    best: dict[str, Any] | None = None
    best_overlap = 0.0
    for candidate in candidates:
        overlap = min(segment["end"], candidate["end"]) - max(segment["start"], candidate["start"])
        if overlap > best_overlap:
            best, best_overlap = candidate, overlap
    if best is None:
        return None
    shorter = min(segment["end"] - segment["start"], best["end"] - best["start"])
    return best if shorter > 0 and best_overlap >= 0.5 * shorter else None


def _risk(whisper: str, text: str) -> float:
    """0 = untouched, 1 = completely rewritten. A proxy for confidence."""
    if not whisper or not text:
        return 0.0
    return round(1 - difflib.SequenceMatcher(None, whisper, text).ratio(), 3)


def build_state(output_dir: Path) -> list[dict[str, Any]]:
    """Assemble the editable model, translated runs included.

    On a translated run the editable text is the Chinese, while the diff that
    tells you whether recognition went wrong belongs to the spoken language.
    Comparing Chinese against English would score every line as fully rewritten,
    so the two are kept apart: `source` holds what was said, `text` what will
    be read.
    """
    subtitles = parse_srt(output_dir / "subtitles_zh.srt")
    qwen_segments = _load_segments(output_dir / "transcript.json")
    raw_segments = _load_segments(output_dir / "transcript_raw.json")
    translated = any(item.get("zh") for item in qwen_segments)

    # Pairing by position only holds while nothing has changed the count. The
    # repair pass divides an over-long caption, so a run can finish with more
    # captions than it recognised -- and then every line after the division is
    # compared against its neighbour's original and scored as fully rewritten.
    aligned = len(raw_segments) == len(subtitles) == len(qwen_segments)

    segments = []
    for index, segment in enumerate(subtitles):
        qwen = (qwen_segments[index] if aligned and index < len(qwen_segments)
                else _best_overlap(segment, qwen_segments))
        whisper = (raw_segments[index] if aligned and translated and index < len(raw_segments)
                   else _best_overlap(segment, raw_segments))
        whisper_text = str(whisper["text"]).strip() if whisper else ""
        qwen_text = str(qwen["text"]).strip() if qwen else ""
        # On a translated run the SRT already holds the Chinese, and the spoken
        # line is the corrected original from transcript.json.
        source_text = qwen_text if translated else ""
        compare_against = source_text if translated else segment["text"]
        segments.append({
            "id": index + 1,
            "start": segment["start"],
            "end": segment["end"],
            "text": segment["text"],
            "source": source_text,
            "whisper": whisper_text,
            "qwen": qwen_text,
            # No overlapping Whisper output means this line came from the
            # reviewed extra-segments sidecar (street or telephone interview).
            "origin": "whisper" if whisper_text else "sidecar",
            "risk": _risk(whisper_text, compare_against),
            # What the run's final review could not settle by counting. It is
            # carried through rather than acted on: every kind it reports is a
            # judgement, and this is the screen where judgements get made.
            "review": (qwen or {}).get("review"),
            "confirmed": False,
        })
    return segments


def find_gaps(segments: list[dict[str, Any]], total: float) -> list[dict[str, Any]]:
    """Stretches with no subtitle at all -- candidates for re-listening."""
    ordered = sorted(segments, key=lambda s: s["start"])
    gaps = []
    cursor = 0.0
    for segment in ordered:
        if segment["start"] - cursor >= MIN_GAP:
            gaps.append({"start": round(cursor, 2), "end": round(segment["start"], 2)})
        cursor = max(cursor, segment["end"])
    if total - cursor >= MIN_GAP:
        gaps.append({"start": round(cursor, 2), "end": round(total, 2)})
    return gaps


def load_state(state_path: Path, output_dir: Path) -> list[dict[str, Any]]:
    """Resume a previous review session, or start one from pipeline output."""
    if state_path.is_file():
        stored = json.loads(state_path.read_text(encoding="utf-8"))
        if stored.get("segments"):
            return stored["segments"]
    return build_state(output_dir)


def save_state(
    state_path: Path,
    srt_path: Path,
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist the session and emit reviewed SRTs. Original files are untouched."""
    ordered = sorted(segments, key=lambda s: (s["start"], s["end"]))
    renumbered = [{**segment, "id": index} for index, segment in enumerate(ordered, start=1)]
    for segment in renumbered:
        compare = segment.get("source") or segment.get("text", "")
        segment["risk"] = _risk(segment.get("whisper", ""), compare)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"segments": renumbered}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_srt(srt_path, renumbered)
    written = {"zh": srt_path}
    if any(segment.get("source") for segment in renumbered):
        bilingual = srt_path.with_name("subtitles_bilingual.reviewed.srt")
        write_srt(bilingual, [
            {**segment,
             "text": f"{segment['text']}\n{segment['source']}".strip()
                     if segment.get("source") else segment["text"]}
            for segment in renumbered
        ])
        written["bilingual"] = bilingual
    return {
        "segments": renumbered,
        "written": {key: str(path) for key, path in written.items()},
        "paths": written,
        "lines": len([s for s in renumbered if str(s.get("text", "")).strip()]),
    }
