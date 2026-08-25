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


def transcribe(
    audio: Path,
    model_name: str,
    context: str = "",
    sensitive: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    raw_segments, info = _model(model_name).transcribe(
        str(audio),
        beam_size=5,
        vad_filter=not sensitive,
        condition_on_previous_text=not sensitive,
        no_speech_threshold=0.3 if sensitive else 0.6,
        log_prob_threshold=-1.5 if sensitive else -1.0,
        initial_prompt=context[:1200] or None,
    )
    converter = OpenCC("s2twp")
    segments = []
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
    return segments, info.language


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
    segments: list[dict[str, Any]], total: float, min_gap: float = GAP_MIN
) -> list[tuple[float, float]]:
    """Stretches of the video carrying no caption at all."""
    gaps = []
    cursor = 0.0
    for segment in sorted(segments, key=lambda item: item["start"]):
        if segment["start"] - cursor >= min_gap:
            gaps.append((cursor, segment["start"]))
        cursor = max(cursor, segment["end"])
    if total - cursor >= min_gap:
        gaps.append((cursor, total))
    return gaps


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
    gaps = find_gaps(segments, total, min_gap)
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


CORRECTION_PROMPT = """你是繁體中文新聞字幕校對員。請校正語音辨識逐字稿中的同音錯字、地名、道路名、機構名與標點。
只能根據原句和上下文校正，不得增加原本沒有說出的資訊。保留每一段的 id、start、end，不得合併、刪除或調整時間。
只輸出 JSON：{"segments":[{"id":1,"start":0.0,"end":1.0,"text":"校正文字"}]}"""


# A correction should be a respelling, not a replacement. Anything that drifts
# this far from what was heard is the model writing its own line.
REWRITE_RATIO = 0.34
# ...and if it also matches the supplied context, it copied the news summary in.
CONTEXT_RATIO = 0.55


def _context_phrases(context: str) -> list[str]:
    """Split the title and description into clause-sized pieces.

    A subtitle line is one clause, so comparing it against whole paragraphs
    always scores low; the copied text has to be matched at the same grain.
    """
    pieces = re.split(r"[，、。？！\n：]+", context)
    return [piece.strip() for piece in pieces if len(piece.strip()) >= 3]


def _fabricated(original: str, corrected: str, context_lines: list[str]) -> bool:
    """True if a correction abandoned the audio and echoed the context instead.

    Given unintelligible speech -- Taiwanese written as Mandarin homophones, say
    -- the model will reach for the nearest usable text, which is the title and
    description handed to it for proper nouns. On a news video that puts words
    into an interviewee's mouth, so such a correction is refused.
    """
    if difflib.SequenceMatcher(None, original, corrected).ratio() >= REWRITE_RATIO:
        return False
    return any(
        difflib.SequenceMatcher(None, corrected, line).ratio() >= CONTEXT_RATIO
        or (len(corrected) >= 4 and corrected in line)
        for line in context_lines
    )


def correct_with_qwen(
    client: OllamaClient,
    segments: list[dict[str, Any]],
    context: str = "",
) -> list[dict[str, Any]]:
    converter = OpenCC("s2twp")
    context_lines = _context_phrases(context)
    prefix = (
        "公開影片標題與說明可用來確認專有名詞，但不可用來增加旁白沒有說的內容：\n"
        + context
        + "\n逐字稿批次：\n"
    )

    def correct_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response = client.chat_json(
            CORRECTION_PROMPT,
            prefix + json.dumps({"segments": batch}, ensure_ascii=False),
        )
        corrected = response.get("segments", [])
        expected_ids = [item["id"] for item in batch]
        returned_ids = [
            int(item["id"])
            for item in corrected
            if isinstance(item, dict) and "id" in item
        ]
        if len(corrected) != len(batch) or sorted(returned_ids) != sorted(expected_ids):
            if len(batch) > 1:
                middle = len(batch) // 2
                return correct_batch(batch[:middle]) + correct_batch(batch[middle:])
            return [{**batch[0], "text": converter.convert(batch[0]["text"])}]
        by_id = {int(item["id"]): item for item in corrected}
        corrected_batch = []
        for original in batch:
            text = str(by_id[original["id"]].get("text", "")).strip() or original["text"]
            text = converter.convert(text)
            if _fabricated(original["text"], text, context_lines):
                print(f"      拒絕竄改 #{original['id']}：{original['text']!r} -> {text!r}")
                text = original["text"]
            corrected_batch.append({**original, "text": text})
        return corrected_batch

    result = []
    for offset in range(0, len(segments), 8):
        result.extend(correct_batch(segments[offset:offset + 8]))
    return result


def save_transcript(segments: list[dict[str, Any]], txt_path: Path, srt_path: Path) -> None:
    txt_path.write_text(
        "\n".join(f"[{timestamp(s['start'])} --> {timestamp(s['end'])}] {s['text']}" for s in segments) + "\n",
        encoding="utf-8",
    )
    blocks = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n{timestamp(segment['start'], True)} --> {timestamp(segment['end'], True)}\n{segment['text']}"
        )
    srt_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


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
