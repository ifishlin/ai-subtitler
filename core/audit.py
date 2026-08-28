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
SPEECH_PEAK = 0.06      # fallback: below this a gap is quiet, not missed speech
MIN_SPOKEN = 0.8        # speech shorter than this inside a gap is a stray word
ECHO_RATIO = 0.8        # near-identical neighbours mean a model looped
NON_SUBTITLE = re.compile(r"[^\x20-\x7e\sÀ-ſ　-〿一-鿿＀-￯‐-⁞]")

# A run is only worth a human's time if something serious is wrong, so findings
# carry a weight and the verdict follows the total.
WEIGHTS = {"missing": 4, "garbled": 4, "echo": 3, "unreadable": 2,
           "overlap": 2, "low-confidence": 1, "flash": 1}
REVIEW_SCORE = 8


def _visible(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def speech_windows(source: Path) -> list[tuple[float, float]] | None:
    """Where someone is actually speaking, per Silero VAD.

    Loudness cannot tell a voice from a title card's music or a burst of
    applause, so measuring it reports missed captions where nothing was said.
    The recogniser already carries a model that can tell the difference -- it
    uses it to skip silence before transcribing -- and asking that model
    directly is both more accurate and no more work.

    None when the model or the audio is unavailable, so the caller falls back
    to loudness rather than losing the check.
    """
    try:
        import numpy
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError:
        return None

    # Where someone speaks does not change, and finding out costs four seconds
    # of decoding and model time -- long enough that the editor's audit button
    # felt broken. The answer is kept beside the video it describes.
    cached = source.parent / f".{source.stem}.speech.json"
    if cached.is_file() and cached.stat().st_mtime >= source.stat().st_mtime:
        try:
            return [(float(a), float(b)) for a, b in json.loads(
                cached.read_text(encoding="utf-8"))]
        except (ValueError, TypeError):
            pass
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(source), "-vn",
         "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        check=False, capture_output=True,
    )
    if not result.stdout:
        return None
    audio = numpy.frombuffer(
        result.stdout[: len(result.stdout) // 2 * 2], dtype=numpy.int16
    ).astype("float32") / 32768.0
    if audio.size == 0:
        return None
    try:
        spans = get_speech_timestamps(
            audio,
            VadOptions(min_speech_duration_ms=250, min_silence_duration_ms=400),
            sampling_rate=16000,
        )
    except Exception:                                             # noqa: BLE001
        return None
    windows = [(span["start"] / 16000, span["end"] / 16000) for span in spans]
    try:
        cached.write_text(json.dumps(windows), encoding="utf-8")
    except OSError:
        pass                              # a read-only directory is not a failure
    return windows


def _spoken_within(windows: list[tuple[float, float]], start: float, end: float) -> float:
    """Seconds of speech inside a stretch with no caption on it."""
    return sum(max(0.0, min(end, finish) - max(start, begin))
               for begin, finish in windows)


def _peaks(source: Path, buckets: int = 2000) -> tuple[list[float], float]:
    """Per-bucket loudness. The fallback when the VAD model is unavailable."""
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
    # Ask what was spoken before falling back to what was loud -- and when the
    # answer comes back, do not also decode the whole track to measure volume
    # nobody is going to consult.
    windows = speech_windows(source) if source else None
    peaks, measured = ([], 0.0)
    if windows is None and source and source.is_file():
        peaks, measured = _peaks(source)
        total = duration or measured
    cursor = 0.0
    covered = 0.0
    for segment in ordered:
        if segment["start"] - cursor >= MIN_GAP:
            if windows is not None:
                spoken = _spoken_within(windows, cursor, segment["start"])
                loud = spoken / max(0.01, segment["start"] - cursor)
                if spoken >= MIN_SPOKEN:
                    note("missing", cursor,
                         f"{segment['start']-cursor:.1f} 秒沒有字幕，其中 {spoken:.1f} 秒有人在說話")
                cursor = max(cursor, segment["end"])
                continue
            loud = _loudest(peaks, measured, cursor, segment["start"]) if peaks else 1.0
            if loud >= SPEECH_PEAK:
                note("missing", cursor,
                     f"{segment['start']-cursor:.1f} 秒沒有字幕，但有聲音（峰值 {loud:.2f}）")
        covered += max(0.0, segment["end"] - max(cursor, segment["start"]))
        cursor = max(cursor, segment["end"])
    if total - cursor >= MIN_GAP and windows is not None:
        spoken = _spoken_within(windows, cursor, total)
        if spoken >= MIN_SPOKEN:
            note("missing", cursor,
                 f"{total-cursor:.1f} 秒沒有字幕，其中 {spoken:.1f} 秒有人在說話")
    elif total - cursor >= MIN_GAP and peaks:
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
