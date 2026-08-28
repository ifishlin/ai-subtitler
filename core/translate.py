"""Translating captions without letting them drift.

Whisper ends a caption where the speaker paused, which is rarely where the
sentence ends. Translating those fragments one by one gives each the meaning of
whatever its neighbour was saying, so the sentences are reassembled first,
translated whole, and divided again along the captions that spoke them -- never
through the middle of a word.
"""
from __future__ import annotations

import json
import re
from typing import Any

from opencc import OpenCC

from .ollama import OllamaClient
from .proofread import _normalise

TRANSLATION_PROMPT = """你是新聞字幕翻譯員。請把每一段英文字幕翻成臺灣用語的繁體中文。
一段對一段，不得合併或拆分，不得增加原文沒有的資訊。人名、地名、機構名使用臺灣新聞慣用譯名。
譯文要像新聞字幕一樣簡潔，每段盡量不超過 20 個中文字。保留每一段的 id。
只輸出 JSON：{"segments":[{"id":1,"text":"譯文"}]}"""


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
