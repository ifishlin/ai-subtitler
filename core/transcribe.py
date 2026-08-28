from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel
from opencc import OpenCC

from .media import slice_audio
from .ollama import OllamaClient
from .utils import timestamp

_MODELS: dict[str, WhisperModel] = {}


def _model(name: str) -> WhisperModel:
    """Whisper models are expensive to load, and a run needs one twice."""
    if name not in _MODELS:
        _MODELS[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODELS[name]


# Text describing a video is not text spoken in it. Handed the title and
# description as an initial_prompt, Whisper opened this project's English
# interview by reciting the channel's own blurb -- "11Alive is the largest news
# channel in the U.S." -- and, conditioned on that, looped for 26 seconds.
# Without it the same audio transcribed cleanly. The metadata still reaches
# Qwen, which needs prose context; the recogniser only needs the audio.
# Proper nouns for the recogniser belong in VIDEO.terms.txt instead.
def transcribe(
    audio: Path,
    model_name: str,
    sensitive: bool = False,
    recut: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    raw_segments, info = _model(model_name).transcribe(
        str(audio),
        beam_size=5,
        vad_filter=not sensitive,
        condition_on_previous_text=not sensitive,
        no_speech_threshold=0.3 if sensitive else 0.6,
        log_prob_threshold=-1.5 if sensitive else -1.0,
        word_timestamps=recut,
    )
    converter = OpenCC("s2twp")
    segments = []
    words: list[dict[str, Any]] = []
    for index, segment in enumerate(raw_segments, start=1):
        text = converter.convert(segment.text.strip())
        if text:
            segments.append({
                "id": index,
                "start": segment.start,
                "end": segment.end,
                "text": text,
                "logprob": round(segment.avg_logprob, 3),
                "origin": "whisper",
            })
        for word in getattr(segment, "words", None) or []:
            piece = converter.convert(str(word.word))
            if piece.strip():
                words.append({
                    "word": piece,
                    "start": float(word.start),
                    "end": float(word.end),
                    "logprob": round(segment.avg_logprob, 3),
                })

    segments = drop_hallucinations(segments)
    if recut and words:
        segments = recut_segments(segments, words)
    return segments, info.language


# A subtitle for a Chinese or English video is built from printable ASCII, Han
# characters and CJK punctuation. Anything else -- replacement characters,
# Cyrillic, Hangul -- is a decode that came off the rails, so it is counted
# rather than matched, letting one accented name through but not a broken line.
ALLOWED_CHARS = re.compile(
    r"[\x20-\x7e\s\u00c0-\u017f\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"
    r"\u2010-\u2027\u2030-\u205e]"
)
# Bytes that only appear when a decode breaks down, never in a real subtitle for
# these languages. One is enough to condemn the line, whereas an accented name
# has to be kept, so these are matched rather than counted.
CORRUPTION = re.compile(r"[\u00ff\u00fe\u00fd\ufffd\u0000-\u001f]")
BROKEN_RATIO = 0.08


def drop_hallucinations(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove lines Whisper invented rather than heard.

    Two signatures, both seen in this project: characters from scripts the video
    never used, and a line repeated as the model loops on its own output. A
    fluent line about something else entirely passes all of this -- that needs
    the content read, which is review_hallucinations().
    """
    kept: list[dict[str, Any]] = []
    dropped = 0
    for segment in segments:
        text = segment["text"]
        if CORRUPTION.search(text):
            dropped += 1
            continue
        broken = len(text) - len(ALLOWED_CHARS.findall(text))
        if broken and broken / len(text) > BROKEN_RATIO:
            dropped += 1
            continue
        if kept and kept[-1]["text"] == text:
            dropped += 1
            continue
        kept.append(segment)
    if dropped:
        print(f"      過濾 {dropped} 段幻覺輸出")
    return [{**item, "id": index} for index, item in enumerate(kept, start=1)]


def recut_segments(
    segments: list[dict[str, Any]], words: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Tidy caption boundaries, keeping Whisper's segments as the unit.

    Word timings say where a division falls in time; they do not replace the
    text. Rebuilding from them was tried and made things worse, because a
    segment boundary is how Whisper marks most of its pauses in Chinese and
    flattening the transcript discards every one.
    """
    from .segment import describe, tidy

    inside = [
        word for word in words
        if any(item["start"] - 0.05 <= word["start"] < item["end"] + 0.05
               for item in segments)
    ]
    adjusted = tidy(segments, inside or None)
    if not adjusted:
        return segments
    for item in adjusted:
        spoken = [w["logprob"] for w in inside
                  if item["start"] <= w["start"] < item["end"]]
        if spoken:
            item["logprob"] = round(sum(spoken) / len(spoken), 3)
        item.setdefault("origin", "whisper")
    print(f"      調整斷句：{describe(segments)} → {describe(adjusted)}")
    return adjusted


# Gap filling: sensitive decoding is only trustworthy over a short window.
# Applied to the whole video it invents words; applied to one silent stretch it
# recovers speech the default pass drops (Taiwanese, telephone, overlapping).
# Whisper decodes with a 448-token budget shared by the prompt and the output,
# and faster-whisper allows hotwords up to half of it, so hotwords are only ever
# safe on the gap pass. Measured on this project's street interview they are not
# worth using even there: they moved avg_logprob from -0.59 to -1.19 and turned
# 中兩次 into 中二度. A term list steers the decoder towards its own vocabulary,
# which is the opposite of what unclear speech needs.
TERMS_MAX = 200        # characters of hotwords, well inside the token budget
GAP_MIN = 1.5          # shortest silence worth revisiting, in seconds
GAP_PAD = 0.35         # widen the window so Whisper has context at the edges
# A noisy street interview legitimately reads 0.66 here, so this only rejects
# windows Whisper is confident carry no speech at all.
NO_SPEECH_MAX = 0.9
LOGPROB_MIN = -1.05    # drop low-confidence guesses
ECHO_RATIO = 0.62      # drop text that merely repeats a nearby caption


def find_gaps(
    segments: list[dict[str, Any]], total: float, min_gap: float = GAP_MIN,
    source: Path | None = None,
) -> list[tuple[float, float]]:
    """Stretches carrying no caption -- and, where the source is available,
    only those where somebody was speaking.

    Every silence used to be revisited, which meant re-running the recogniser
    over title music and applause and hoping the filters caught whatever it
    dreamt up there. Asking the voice-activity model first means the second
    pass only listens where there is a voice to hear.
    """
    gaps = []
    cursor = 0.0
    for segment in sorted(segments, key=lambda item: item["start"]):
        if segment["start"] - cursor >= min_gap:
            gaps.append((cursor, segment["start"]))
        cursor = max(cursor, segment["end"])
    if total - cursor >= min_gap:
        gaps.append((cursor, total))
    if source is None:
        return gaps

    from .audit import MIN_SPOKEN, _spoken_within, speech_windows
    windows = speech_windows(source)
    if windows is None:
        return gaps
    return [(start, end) for start, end in gaps
            if _spoken_within(windows, start, end) >= MIN_SPOKEN]


def _echoes_neighbour(text: str, segments: list[dict[str, Any]], at: float) -> bool:
    """True if this text just repeats a caption near the same moment.

    Sensitive decoding on near-silence sometimes reproduces narration from
    elsewhere in the video; such a line is a hallucination, not a recovery.
    """
    nearby = sorted(segments, key=lambda item: abs(item["start"] - at))[:6]
    return any(
        difflib.SequenceMatcher(None, text, item["text"]).ratio() >= ECHO_RATIO
        for item in nearby
    )


def fill_gaps(
    source: Path,
    work_dir: Path,
    model_name: str,
    segments: list[dict[str, Any]],
    total: float,
    terms: str = "",
    min_gap: float = GAP_MIN,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-transcribe only the silent stretches, in sensitive mode.

    Returns the merged segments and a per-gap report of what was kept and why
    anything was dropped, so a run can be judged rather than just trusted.
    """
    converter = OpenCC("s2twp")
    gaps = find_gaps(segments, total, min_gap, source=source)
    recovered: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []

    for index, (start, end) in enumerate(gaps, start=1):
        window_start = max(0.0, start - GAP_PAD)
        window_end = min(total, end + GAP_PAD)
        clip = slice_audio(source, window_start, window_end, work_dir / f"gap_{index:02}.wav")
        found, dropped = [], []
        raw_segments, _ = _model(model_name).transcribe(
            str(clip),
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=False,
            no_speech_threshold=0.3,
            log_prob_threshold=-1.5,
            hotwords=terms or None,
        )
        window_kept = 0
        for segment in raw_segments:
            text = converter.convert(segment.text.strip())
            at = window_start + segment.start
            if not text:
                continue
            if segment.no_speech_prob > NO_SPEECH_MAX:
                dropped.append((text, f"判定為靜音 {segment.no_speech_prob:.2f}"))
            elif segment.avg_logprob < LOGPROB_MIN:
                dropped.append((text, f"信心過低 {segment.avg_logprob:.2f}"))
            elif _echoes_neighbour(text, segments + recovered, at):
                dropped.append((text, "與鄰近字幕重複，判定為幻覺"))
            else:
                # The window is padded for context, so clamp the result back
                # inside the gap; otherwise a recovered line overlaps the
                # caption whose edge supplied that context.
                at_end = window_start + segment.end
                clamped_start = max(start, min(at, end))
                clamped_end = min(end, max(at_end, clamped_start))
                if clamped_end - clamped_start < 0.3:
                    dropped.append((text, "裁回空隙內後長度不足"))
                    continue
                found.append({
                    "id": 0,
                    "start": round(clamped_start, 2),
                    "end": round(clamped_end, 2),
                    "text": text,
                    "logprob": round(segment.avg_logprob, 3),
                    "origin": "gap",
                })
        recovered.extend(found)
        report.append({
            "window": (round(start, 2), round(end, 2)),
            "kept": found,
            "dropped": dropped,
        })

    merged = sorted(
        [{**item} for item in segments] + recovered,
        key=lambda item: (item["start"], item["end"]),
    )
    return [{**item, "id": number} for number, item in enumerate(merged, start=1)], report












































