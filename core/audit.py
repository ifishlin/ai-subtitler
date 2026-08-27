"""Judge a finished subtitle set, so only the doubtful runs need eyes.

A pipeline that cannot assess its own output has to be reviewed video by video,
which is the thing that does not scale. Most of what goes wrong is measurable:
a caption too dense to read, a stretch of speech with no caption at all, a line
the recogniser was unsure of, text repeated because a model looped. Those are
checked here, deterministically, and each finding names the moment it occurs so
it can be opened directly.

What remains is judgement -- whether a merge changed the meaning, whether a
translation says what was said -- and that is what an LLM review adds on top.
This layer decides whether such a review is even needed.
"""
from __future__ import annotations

import array
import difflib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

# The readable length of a line depends on the script it is written in, so the
# limits come from the captions themselves -- checking English against a Chinese
# budget flagged every line and put this project's score at 551.
from .segment import limits_for

MAX_SECONDS = 6.0
MIN_SECONDS = 0.9
# In an argument the speakers talk over each other, and a line that lasts half a
# second is the interview, not a defect. Only a caption well past what a viewer
# can follow is worth reporting, so the thresholds that flag pace carry margin
# the hard faults do not.
FLASH_SECONDS = 0.4
CPS_MARGIN = 1.6
LOW_CONFIDENCE = -0.9
MIN_GAP = 1.5           # silence shorter than this needs no caption
SPEECH_PEAK = 0.06      # below this a gap is genuinely quiet, not missed speech
ECHO_RATIO = 0.8        # near-identical neighbours mean a model looped
NON_SUBTITLE = re.compile(r"[^\x20-\x7e\sÀ-ſ　-〿一-鿿＀-￯‐-⁞]")

# A run is only worth a human's time if something serious is wrong, so findings
# carry a weight and the verdict follows the total.
WEIGHTS = {"missing": 4, "garbled": 4, "echo": 3, "unreadable": 2,
           "overlap": 2, "low-confidence": 1, "flash": 1}
REVIEW_SCORE = 8


def _visible(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _peaks(source: Path, buckets: int = 2000) -> tuple[list[float], float]:
    """Per-bucket loudness, used to tell a silent gap from a missed line."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(source), "-vn",
         "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        check=True, capture_output=True,
    )
    samples = array.array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    if not samples:
        return [], 0.0
    duration = len(samples) / 8000
    size = max(1, len(samples) // buckets)
    peaks = [max(max(w), -min(w)) / 32768
             for w in (samples[i:i + size] for i in range(0, len(samples), size)) if w]
    return peaks, duration


def _loudest(peaks: list[float], duration: float, start: float, end: float) -> float:
    if not peaks or duration <= 0:
        return 0.0
    first = int(len(peaks) * start / duration)
    last = max(first + 1, int(len(peaks) * end / duration))
    return max(peaks[first:last], default=0.0)


def inspect(
    segments: list[dict[str, Any]], source: Path | None = None, duration: float | None = None
) -> dict[str, Any]:
    """Return {verdict, score, findings, summary} for one subtitle set."""
    limits = limits_for(segments)
    max_chars, max_cps = int(limits["chars"]), limits["cps"]
    findings: list[dict[str, Any]] = []

    def note(kind: str, at: float, detail: str) -> None:
        findings.append({"kind": kind, "at": round(at, 1), "detail": detail})

    ordered = sorted(segments, key=lambda s: (s["start"], s["end"]))

    for segment in ordered:
        text = str(segment.get("text", ""))
        span = segment["end"] - segment["start"]
        chars = _visible(text)
        if NON_SUBTITLE.search(text):
            note("garbled", segment["start"], f"含非字幕字元：{text[:24]}")
        if chars > max_chars:
            note("unreadable", segment["start"], f"{chars} 字，超過 {max_chars}：{text[:24]}")
        if span > MAX_SECONDS:
            note("unreadable", segment["start"], f"停留 {span:.1f} 秒：{text[:24]}")
        if span < FLASH_SECONDS:
            note("flash", segment["start"], f"只顯示 {span:.1f} 秒：{text[:24]}")
        if span > 0 and chars / span > max_cps * CPS_MARGIN:
            note("unreadable", segment["start"],
                 f"每秒 {chars/span:.1f} 字，遠超 {max_cps:.0f}：{text[:24]}")
        if segment.get("logprob", 0.0) < LOW_CONFIDENCE:
            note("low-confidence", segment["start"],
                 f"信心 {segment['logprob']:.2f}：{text[:24]}")

    for earlier, later in zip(ordered, ordered[1:]):
        if later["start"] < earlier["end"] - 0.05:
            note("overlap", earlier["start"],
                 f"與下一段重疊 {earlier['end']-later['start']:.1f} 秒")
        ratio = difflib.SequenceMatcher(None, earlier["text"], later["text"]).ratio()
        if ratio >= ECHO_RATIO:
            note("echo", later["start"], f"與前一段幾乎相同：{later['text'][:24]}")

    # A gap only matters where something was being said.
    total = duration or (ordered[-1]["end"] if ordered else 0.0)
    peaks, measured = ([], 0.0)
    if source and source.is_file():
        peaks, measured = _peaks(source)
        total = duration or measured
    cursor = 0.0
    covered = 0.0
    for segment in ordered:
        if segment["start"] - cursor >= MIN_GAP:
            loud = _loudest(peaks, measured, cursor, segment["start"]) if peaks else 1.0
            if loud >= SPEECH_PEAK:
                note("missing", cursor,
                     f"{segment['start']-cursor:.1f} 秒沒有字幕，但有聲音（峰值 {loud:.2f}）")
        covered += max(0.0, segment["end"] - max(cursor, segment["start"]))
        cursor = max(cursor, segment["end"])
    if total - cursor >= MIN_GAP and peaks:
        loud = _loudest(peaks, measured, cursor, total)
        if loud >= SPEECH_PEAK:
            note("missing", cursor, f"結尾 {total-cursor:.1f} 秒沒有字幕，但有聲音")

    score = sum(WEIGHTS.get(f["kind"], 1) for f in findings)
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding["kind"]] = counts.get(finding["kind"], 0) + 1
    return {
        "verdict": "review" if score >= REVIEW_SCORE else "pass",
        "score": score,
        "limits": f"每行上限 {max_chars} 字、每秒 {max_cps:.0f} 字",
        "segments": len(ordered),
        "coverage": round(covered / total, 3) if total else 0.0,
        "counts": counts,
        "findings": sorted(findings, key=lambda f: (-WEIGHTS.get(f["kind"], 1), f["at"])),
    }


def report(result: dict[str, Any], limit: int = 12) -> str:
    lines = [
        f"品質判定：{'需要人工檢查' if result['verdict'] == 'review' else '可發布'}"
        f"（分數 {result['score']}，門檻 {REVIEW_SCORE}）",
        f"  {result['segments']} 段，字幕覆蓋 {result['coverage']:.0%}"
        + (f"（{result['limits']}）" if result.get("limits") else ""),
    ]
    if result["counts"]:
        lines.append("  " + "、".join(f"{k} {v}" for k, v in result["counts"].items()))
    for finding in result["findings"][:limit]:
        lines.append(f"    {finding['at']:>7.1f}s  [{finding['kind']}] {finding['detail']}")
    if len(result["findings"]) > limit:
        lines.append(f"    （另有 {len(result['findings']) - limit} 項）")
    return "\n".join(lines)


def write(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
