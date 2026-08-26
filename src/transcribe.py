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


HALLUCINATION_PROMPT = """你是新聞字幕的審查員。以下逐字稿由語音辨識產生，其中可能混入
「模型憑空生成」的句子——它讀起來通順，但與影片主題完全無關，或屬於另一種語言／另一個節目。

典型例子：一則臺灣水災新聞裡出現「多謝您收睇時局新聞，再會!」（粵語、且是別的節目的結語）。

請只列出你確定是憑空生成的段落編號。判斷標準：
- 與前後文的主題完全無關，不是話題轉換而是完全不相干
- 屬於影片語言之外的語言，或是其他節目的固定用語
- 不確定就不要列出。少刪一句無妨，誤刪真實內容才是嚴重問題

只輸出 JSON：{"hallucinated":[{"id":34,"why":"粵語，且為其他節目結語"}]}"""


CORRECTION_PROMPT = """你是繁體中文新聞字幕校對員。請找出語音辨識逐字稿中的同音錯字與標點問題。

你不能重寫句子，只能列出「要把哪幾個字換成哪幾個字」。
每一筆修改的 from 必須是該段文字中「原封不動出現過」的片段，否則會被丟棄。

請仔細找出每一處錯誤，常見的有：
- 同音或近音錯字：「攤方」→「坍方」、「水深即稀」→「水深及膝」、「喪亡」→「傷亡」
- 地名路名聽錯：「阿聯」→「阿蓮」、「大人街」→「大仁街」、「入口閘道」→「入口匝道」
- 數字與單位：「屏東三線」→「屏東3縣市」、「國一」→「國1」
- 口吃或重複：「低氣 低壓帶」→「低壓帶」
- 缺少標點：「臺南高雄及屏東」→「臺南、高雄及屏東」

限制：
- 不得搬動內容：某一段的文字絕對不可以換成另一段的內容。
- 不得只是把同樣的字重新排列。
- 替換前後的讀音必須相近；讀音差很多就表示你在改寫語意，不是校對。
- 人名、地名、機構名若已是真實存在的名稱，保留原樣。你的知識可能過時，字幕必須忠實反映影片所說。
- 這一批確實沒有錯才回傳空陣列。

只輸出 JSON：{"edits":[{"id":1,"from":"錯的片段","to":"正確片段"}]}"""

CORRECTION_PROMPT_EN = """You are a news subtitle proofreader working in English. Find mis-heard
words, spelling and punctuation problems in this speech-recognition transcript.

You may not rewrite a line. List only which fragments to replace.
Every "from" must appear verbatim in that line's text, or the edit is discarded.

Rules:
- Repair only what was mis-heard, e.g. "dee day" -> "D-Day", "water shed" -> "watershed".
- Punctuation and capitalisation may be fixed.
- Never move content: one line's text must never become another line's content.
- If a personal name, job title or organisation is already a real name, leave it. Your knowledge
  may be out of date, and the subtitle must reflect what the video actually said.
- Return an empty array when a batch is already correct. Do not invent work.

Reply with JSON only: {"edits":[{"id":1,"from":"wrong fragment","to":"right fragment"}]}"""

TRANSLATION_PROMPT = """你是新聞字幕翻譯員。請把每一段英文字幕翻成臺灣用語的繁體中文。
一段對一段，不得合併或拆分，不得增加原文沒有的資訊。人名、地名、機構名使用臺灣新聞慣用譯名。
譯文要像新聞字幕一樣簡潔，每段盡量不超過 20 個中文字。保留每一段的 id。
只輸出 JSON：{"segments":[{"id":1,"text":"譯文"}]}"""


def prompt_for(language: str) -> str:
    """Proofreading has to be done in the language actually spoken."""
    return CORRECTION_PROMPT if language.startswith("zh") else CORRECTION_PROMPT_EN


# An edit that names a fragment the line does not contain is not a correction of
# that line -- it is the model writing something else -- so it is refused. This
# makes the failures seen while building this pipeline structurally impossible:
# text cannot migrate between segments, words cannot appear from nowhere, and a
# line cannot be replaced wholesale, because every change must point at
# characters already present.
MAX_EDIT_GROWTH = 3.0      # an edit may not balloon a fragment beyond this
MAX_EDITED_SHARE = 0.6     # nor rewrite more than this share of one line
# Structure alone cannot tell a homophone repair from a same-length rewrite:
# both replace a fragment that is really there. Sound can. A mis-heard word and
# its repair are pronounced alike -- 攤方 and 坍方 are both "tan fang" -- while
# 傳出淹水災情 becoming 無人路透 shares no syllable at all. Research on Chinese
# ASR correction reaches the same conclusion: semantics alone makes it worse,
# and pinyin is what separates the two.
SOUND_RATIO = 0.55


def _sound(text: str) -> str:
    """Mandarin pronunciation, with non-Han characters kept as themselves."""
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        return ""
    return " ".join(lazy_pinyin(text, style=Style.NORMAL))


def _sounds_alike(before: str, after: str) -> bool:
    """Whether a replacement could plausibly be what was actually said.

    Punctuation-only edits are identical in sound and always pass. When pinyin
    is unavailable the check abstains rather than blocking every edit.
    """
    first, second = _sound(before), _sound(after)
    if not first or not second:
        return True
    return difflib.SequenceMatcher(None, first, second).ratio() >= SOUND_RATIO


def apply_edits(
    segments: list[dict[str, Any]],
    edits: list[dict[str, Any]],
    converter: OpenCC,
    chinese: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply verified substitutions, reporting whatever was refused and why."""
    by_id = {item["id"]: dict(item) for item in segments}
    refused: list[str] = []

    for edit in edits:
        if not isinstance(edit, dict):
            continue
        try:
            target = by_id[int(edit["id"])]
        except (KeyError, TypeError, ValueError):
            refused.append(f"未知的 id {edit.get('id')!r}")
            continue

        source = str(edit.get("from", ""))
        replacement = _normalise(str(edit.get("to", "")), converter, chinese)
        if not source:
            refused.append(f"#{target['id']} 沒有指出要改哪一段文字")
            continue
        if source not in target["text"]:
            refused.append(f"#{target['id']} 原文沒有「{source}」，不套用")
            continue
        if len(replacement) > max(4, len(source) * MAX_EDIT_GROWTH):
            refused.append(f"#{target['id']}「{source}」→「{replacement}」擴張過大")
            continue

        if sorted(source) == sorted(replacement) and source != replacement:
            # The same characters in a different order is a reordering, not a
            # mis-hearing; it is how 中華一路跟華泰 became 華泰一路跟中華.
            refused.append(f"#{target['id']}「{source}」→「{replacement}」只是字序對調")
            continue
        sound_checked = chinese and bool(_sound(source)) and bool(_sound(replacement))
        if sound_checked and not _sounds_alike(source, replacement):
            refused.append(
                f"#{target['id']}「{source}」→「{replacement}」讀音不符，非同音錯字"
            )
            continue

        updated = target["text"].replace(source, replacement)
        # A long repair that still sounds the same is a repair; only where the
        # sound could not be compared does sheer size stand in for judgement.
        if not sound_checked:
            changed = abs(len(updated) - len(target["text"])) + len(source)
            if changed > len(target["text"]) * MAX_EDITED_SHARE + 4:
                refused.append(
                    f"#{target['id']}「{source}」→「{replacement}」改動範圍過大，視為重寫"
                )
                continue
        target["text"] = updated

    return [by_id[item["id"]] for item in segments], refused


MAX_HALLUCINATION_SHARE = 0.1


def review_hallucinations(
    client: Any, segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Drop lines a model invented, as judged by reading them.

    The mechanical filter catches broken characters and loops. What it cannot
    see is a line that is fluent, correctly encoded and about something else
    entirely -- this project's flood report acquired a Cantonese sign-off from
    another programme. Judging that needs the content read.

    The reviewer returns ids only, never text, and may not remove more than a
    tenth of the transcript: a misfiring review should cost a caption, not the
    whole run.
    """
    if not segments:
        return segments
    payload = [{"id": s["id"], "text": s["text"]} for s in segments]
    try:
        reply = client.chat_json(
            HALLUCINATION_PROMPT,
            json.dumps({"segments": payload}, ensure_ascii=False),
        )
        flagged = reply.get("hallucinated", [])
    except Exception as error:                                    # noqa: BLE001
        print(f"      幻覺審查失敗，保留全部段落：{error}")
        return segments

    known = {s["id"] for s in segments}
    drop: dict[int, str] = {}
    for item in flagged if isinstance(flagged, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if number in known:
            drop[number] = str(item.get("why", ""))

    limit = max(1, int(len(segments) * MAX_HALLUCINATION_SHARE))
    if len(drop) > limit:
        print(f"      幻覺審查想刪 {len(drop)} 段，超過上限 {limit}，全部不採用")
        return segments
    for number, why in drop.items():
        print(f"      刪除幻覺 #{number}：{why}")
    kept = [s for s in segments if s["id"] not in drop]
    return [{**s, "id": i} for i, s in enumerate(kept, start=1)]


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
    """Proofread by applying verified substitutions rather than rewritten lines.

    Asked to return corrected text, the model rewrote freely: it moved one
    caption's words onto another, replaced a correct road name with a different
    one, and pasted the video's own description into an interviewee's mouth.
    Each needed its own detector. Asked instead for a list of fragments to
    replace, every change has to point at characters the line already contains,
    so that entire class of failure cannot be expressed.
    """
    chinese = language.startswith("zh")
    converter = OpenCC("s2twp")
    system_prompt = prompt_for(language)
    prefix = (
        "公開影片標題與說明可用來確認專有名詞，但不可用來增加旁白沒有說的內容：\n"
        + context
        + "\n逐字稿批次：\n"
    )

    applied = 0
    refusals: list[str] = []

    def correct_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        nonlocal applied
        payload = [{"id": item["id"], "text": item["text"]} for item in batch]
        try:
            response = client.chat_json(
                system_prompt,
                prefix + json.dumps({"segments": payload}, ensure_ascii=False),
            )
            edits = response.get("edits", [])
            if not isinstance(edits, list):
                edits = []
        except Exception as error:                                # noqa: BLE001
            print(f"      校正失敗（{len(batch)} 段）：{error}")
            edits = []

        corrected, refused = apply_edits(batch, edits, converter, chinese)
        applied += sum(1 for before, after in zip(batch, corrected)
                       if before["text"] != after["text"])
        refusals.extend(refused)
        # Traditional-character normalisation applies whether or not an edit landed.
        return [{**item, "text": _normalise(item["text"], converter, chinese)}
                for item in corrected]

    result = []
    for offset in range(0, len(segments), 8):
        result.extend(correct_batch(segments[offset:offset + 8]))

    print(f"      套用 {applied} 段修改，退回 {len(refusals)} 筆無效修改")
    for note in refusals[:12]:
        print(f"        退回：{note}")
    if len(refusals) > 12:
        print(f"        （另有 {len(refusals) - 12} 筆）")
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
