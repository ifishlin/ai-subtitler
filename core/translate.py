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


CUT_BONUS = 4            # how far out of its way a division goes for punctuation
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


def _word_edges(text: str) -> set[int]:
    """Offsets where a Chinese word begins, per jieba.

    Chinese has no spaces, so a proportional cut lands mid-word as readily as
    between words -- 疫情 divided into 疫 and 情 across two captions reads as a
    typing error. The segmenter is the only thing that knows the difference,
    and its answer is cached per sentence because a sentence is divided as many
    times as it has captions.
    """
    import jieba
    # jieba is trained on Simplified Chinese and mis-segments Traditional --
    # 或許能 comes back as 或 and 許能, which is exactly the kind of division
    # this is meant to prevent. Converting for the segmenter's benefit only is
    # safe while the conversion is character-for-character; where it is not,
    # the original is used and a slightly worse boundary is accepted.
    simplified = OpenCC("t2s").convert(text)
    subject = simplified if len(simplified) == len(text) else text
    edges, at = {0}, 0
    for token in jieba.cut(subject):
        at += len(token)
        edges.add(at)
    return edges


def _splits_a_word(text: str, index: int, edges: set[int] | None = None) -> bool:
    """True if cutting here would break a word -- Latin (COVI / D) or Chinese."""
    if index <= 0 or index >= len(text):
        return False
    if text[index - 1].isalnum() and text[index].isalnum() \
            and (text[index - 1].isascii() or text[index].isascii()):
        return True
    if edges is not None and index not in edges:
        return True
    return False


def _cut_at(text: str, target: int, after: int = 0) -> int | None:
    """A split point near target: punctuation first, then any spot that does not
    break a word. None means this sentence should not be divided at all.

    `after` is where the previous cut fell. Divisions have to march forwards --
    searching around a target can otherwise land back before the last one, and
    a sentence divided out of order is not divided at all."""
    if after + 1 >= len(text):
        return None
    target = max(after + 1, min(len(text) - 1, target))

    # Every place the sentence could be divided, ranked by how close it is to
    # where the captions say it should be. Searching a narrow window around the
    # target instead meant that when nothing there was a word boundary -- which
    # is most of the time in Chinese -- the cut fell wherever it landed, and
    # 或許 came out as 或 and 許.
    edges = sorted(index for index in _word_edges(text) if after < index < len(text))
    if edges:
        # Punctuation is preferred, but only nearby: taking a comma wherever it
        # happens to be drags the first division far from where the captions
        # want it and squeezes every division after it into what is left.
        def cost(index: int) -> int:
            return abs(index - target) - (CUT_BONUS if text[index - 1] in CUT_MARKS else 0)
        return min(edges, key=cost)

    # No word boundary left. A division that has to happen is better made
    # somewhere than not made at all.
    for index in _nearby(target, len(text)):
        if index > after and not _splits_a_word(text, index):
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
    cuts: list[int] = []
    used = 0
    for weight in weights[:-1]:
        used += weight
        cut = _cut_at(chinese, round(len(chinese) * used / total),
                      after=cuts[-1] if cuts else 0)
        if cut is None:
            # No readable division is left; show the sentence on its first
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
