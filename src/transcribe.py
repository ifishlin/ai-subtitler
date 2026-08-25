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
) -> tuple[list[dict[str, Any]], str]:
    raw_segments, info = _model(model_name).transcribe(
        str(audio),
        beam_size=5,
        vad_filter=not sensitive,
        condition_on_previous_text=not sensitive,
        no_speech_threshold=0.3 if sensitive else 0.6,
        log_prob_threshold=-1.5 if sensitive else -1.0,
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
    return drop_hallucinations(segments), info.language


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
    never used, and a line repeated as the model loops on its own output.
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
只能修正「同音字」造成的錯誤。人名、職稱、機構名若已寫成一個真實存在的名字，一律保留原樣，
絕對不可以換成你認為「應該是」的另一個名字——你的知識可能過時，而字幕必須忠實反映影片所說。
只輸出 JSON：{"segments":[{"id":1,"start":0.0,"end":1.0,"text":"校正文字"}]}"""

CORRECTION_PROMPT_EN = """You are a news subtitle proofreader working in English. Fix mis-heard words,
spelling, capitalisation and punctuation in this speech-recognition transcript.
Correct only from the line itself and its context; never add information that was not spoken.
Only repair words that were mis-heard. If a personal name, job title or organisation is already
spelled as a real name, keep it exactly as written. Never replace one name with a different name
you believe is more likely: your knowledge may be out of date, and the subtitle must reflect what
the video actually said. Joining a mis-heard spelling back together is allowed ("dee day" -> "D-Day");
swapping the person is not.
Keep every id, start and end unchanged, and do not merge, drop or retime any line.
Reply with JSON only: {"segments":[{"id":1,"start":0.0,"end":1.0,"text":"corrected text"}]}"""

TRANSLATION_PROMPT = """你是新聞字幕翻譯員。請把每一段英文字幕翻成臺灣用語的繁體中文。
一段對一段，不得合併或拆分，不得增加原文沒有的資訊。人名、地名、機構名使用臺灣新聞慣用譯名。
譯文要像新聞字幕一樣簡潔，每段盡量不超過 20 個中文字。保留每一段的 id。
只輸出 JSON：{"segments":[{"id":1,"text":"譯文"}]}"""


def prompt_for(language: str) -> str:
    """Proofreading has to be done in the language actually spoken."""
    return CORRECTION_PROMPT if language.startswith("zh") else CORRECTION_PROMPT_EN


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


NAME_TOKEN = re.compile(r"\b[A-Z][a-z]{2,}\b")


def _heard_forms(text: str) -> set[str]:
    """Every word in the line, plus each adjacent pair joined.

    Whisper splits an unfamiliar name across words ("Bay Jing", "dee day"), so
    the joined pair has to count as something that was heard.
    """
    words = [word.lower() for word in re.findall(r"[A-Za-z]{2,}", text)]
    return set(words) | {a + b for a, b in zip(words, words[1:])}


def _swapped_names(original: str, corrected: str) -> list[str]:
    """Capitalised words introduced by the correction that were never heard.

    Asked to fix names, the model will replace a correct one with whoever held
    the post when it was trained. Respelling something mis-heard keeps letters
    in common, so only wholly unrelated words are reported.
    """
    before = _heard_forms(original)
    invented = []
    for token in NAME_TOKEN.findall(corrected):
        lowered = token.lower()
        if lowered in before:
            continue
        if any(difflib.SequenceMatcher(None, lowered, form).ratio() >= 0.6 for form in before):
            continue
        invented.append(token)
    return invented


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


def _normalise(text: str, converter: OpenCC, chinese: bool) -> str:
    return converter.convert(text) if chinese else text


# Whisper cuts a caption when the speaker pauses, not when the sentence ends, so
# a line is often half a clause ("the last time.", "is that we need to"). Asked
# to translate those one by one, the model translates the sentence it can see
# rather than the fragment it was given, and every translation lands one line
# early. Sentences are therefore reassembled before translation and the result
# divided back across the original timings.
SENTENCE_END = re.compile(r"""[.!?。！？](["'”』」)\]]*)$""")
MAX_GROUP = 5          # never merge more than this many captions
CUT_SEARCH = 4         # characters either side of the ideal cut to look for punctuation
CUT_MARKS = "，、。；：？！,;:"


def _group_sentences(segments: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for segment in segments:
        current.append(segment)
        if SENTENCE_END.search(segment["text"].strip()) or len(current) >= MAX_GROUP:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _splits_a_word(text: str, index: int) -> bool:
    """True if cutting here would break a run of letters or digits.

    Chinese has no spaces, so a proportional cut lands mid-token as readily as
    between tokens; a Latin word or number is the case where that is unreadable
    (COVI / D), and the only case detectable without a word segmenter.
    """
    if index <= 0 or index >= len(text):
        return False
    return text[index - 1].isalnum() and text[index].isalnum() \
        and (text[index - 1].isascii() or text[index].isascii())


def _cut_at(text: str, target: int) -> int | None:
    """A split point near target: punctuation first, then any spot that does not
    break a word. None means this sentence should not be divided at all."""
    target = max(1, min(len(text) - 1, target))
    for index in _nearby(target, len(text)):
        if text[index - 1] in CUT_MARKS and not _splits_a_word(text, index):
            return index
    for index in _nearby(target, len(text)):
        if not _splits_a_word(text, index):
            return index
    return None


def _nearby(target: int, length: int) -> list[int]:
    seen = []
    for offset in range(CUT_SEARCH + 1):
        for index in (target - offset, target + offset):
            if 0 < index < length and index not in seen:
                seen.append(index)
    return seen


def _split_translation(chinese: str, members: list[dict[str, Any]]) -> list[str]:
    """Divide one sentence's translation across the captions that spoke it."""
    if len(members) == 1:
        return [chinese]
    weights = [max(1, len(item["text"])) for item in members]
    total = sum(weights)
    cuts = []
    used = 0
    for weight in weights[:-1]:
        used += weight
        cut = _cut_at(chinese, round(len(chinese) * used / total))
        if cut is None or (cuts and cut <= cuts[-1]):
            # No readable division exists; show the sentence on its first
            # caption rather than breaking a word across two.
            return [chinese] + [""] * (len(members) - 1)
        cuts.append(cut)

    parts, cursor = [], 0
    for cut in cuts:
        parts.append(chinese[cursor:cut].strip())
        cursor = cut
    parts.append(chinese[cursor:].strip())
    return parts


def translate_with_qwen(
    client: OllamaClient,
    segments: list[dict[str, Any]],
    context: str = "",
) -> list[dict[str, Any]]:
    """Add a Traditional Chinese rendering of each line, as segment["zh"].

    Lines the model drops or returns empty keep no translation rather than a
    guess, so a failed batch shows up as a missing subtitle instead of wrong one.
    """
    converter = OpenCC("s2twp")
    prefix = "影片標題與說明，可用來確認專有名詞譯名：\n" + context + "\n英文字幕批次：\n"
    translated = []
    def tidy(text: str) -> str:
        return text.rstrip(" .。").replace(",", "，").replace(". ", "。")

    def translate_batch(batch: list[dict[str, Any]]) -> dict[int, str]:
        """Translate one batch, verifying the ids come back as they went out.

        A reply that renumbers or drops lines silently shifts every subtitle
        onto the wrong moment, so a mismatched batch is split and retried
        rather than trusted -- the same rule the proofreading pass applies.
        """
        payload = [{"id": item["id"], "text": item["text"]} for item in batch]
        try:
            response = client.chat_json(
                TRANSLATION_PROMPT,
                prefix + json.dumps({"segments": payload}, ensure_ascii=False),
            )
            returned = [
                item for item in response.get("segments", [])
                if isinstance(item, dict) and "id" in item
            ]
        except Exception as error:                                # noqa: BLE001
            print(f"      翻譯失敗（{len(batch)} 段）：{error}")
            returned = []

        expected = sorted(item["id"] for item in batch)
        if sorted(int(item["id"]) for item in returned) != expected:
            if len(batch) > 1:
                middle = len(batch) // 2
                return {**translate_batch(batch[:middle]), **translate_batch(batch[middle:])}
            print(f"      翻譯對位失敗 #{batch[0]['id']}，保留原文")
            return {}
        return {
            int(item["id"]): tidy(converter.convert(str(item.get("text", "")).strip()))
            for item in returned
        }

    groups = _group_sentences(segments)
    # One sentence per unit of translation; ids address the sentence, not the caption.
    units = [
        {"id": index, "text": " ".join(item["text"].strip() for item in group)}
        for index, group in enumerate(groups, start=1)
    ]
    print(f"      {len(segments)} 段字幕併成 {len(units)} 個句子送翻")

    for offset in range(0, len(units), 8):
        batch = units[offset:offset + 8]
        by_id = translate_batch(batch)
        for unit in batch:
            group = groups[unit["id"] - 1]
            chinese = by_id.get(unit["id"], "")
            parts = _split_translation(chinese, group) if chinese else [""] * len(group)
            for item, part in zip(group, parts):
                translated.append({**item, "zh": part} if part else {**item})

    missing = sum(1 for item in translated if not item.get("zh"))
    if missing:
        print(f"      有 {missing} 段沒有翻譯，將只顯示原文")
    return translated


def correct_with_qwen(
    client: OllamaClient,
    segments: list[dict[str, Any]],
    context: str = "",
    language: str = "zh",
) -> list[dict[str, Any]]:
    chinese = language.startswith("zh")
    converter = OpenCC("s2twp")
    system_prompt = prompt_for(language)
    context_lines = _context_phrases(context)
    prefix = (
        "公開影片標題與說明可用來確認專有名詞，但不可用來增加旁白沒有說的內容：\n"
        + context
        + "\n逐字稿批次：\n"
    )

    def correct_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        response = client.chat_json(
            system_prompt,
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
            return [{**batch[0], "text": _normalise(batch[0]["text"], converter, chinese)}]
        by_id = {int(item["id"]): item for item in corrected}
        corrected_batch = []
        for original in batch:
            text = str(by_id[original["id"]].get("text", "")).strip() or original["text"]
            text = _normalise(text, converter, chinese)
            invented = [] if chinese else _swapped_names(original["text"], text)
            if invented:
                print(f"      拒絕換名 #{original['id']}：{'、'.join(invented)} 未出現在原句")
                text = original["text"]
            elif _fabricated(original["text"], text, context_lines):
                print(f"      拒絕竄改 #{original['id']}：{original['text']!r} -> {text!r}")
                text = original["text"]
            corrected_batch.append({**original, "text": text})
        return corrected_batch

    result = []
    for offset in range(0, len(segments), 8):
        result.extend(correct_batch(segments[offset:offset + 8]))
    return result


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
