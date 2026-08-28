"""Proofreading a transcript by verified substitution.

Asked for corrected text, a model rewrites: it moves one caption's words onto
another, replaces a road name with a different road name, pastes the video's
own description into an interviewee's mouth. Each of those needed its own
detector, and each detector was written after the fact.

Asked instead for a list of fragments to replace, the whole class of failure
becomes unsayable -- every change has to point at characters the line already
contains, and every one is checked for sounding like what it replaces before it
lands. What a stronger model buys is a better hit rate, not permission to skip
the checking.
"""
from __future__ import annotations

import difflib
import json
import re
from typing import Any
from opencc import OpenCC

from .ollama import OllamaClient


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
