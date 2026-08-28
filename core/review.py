"""The last read, over what will actually ship.

Proofreading and translation are edits, and edits go wrong: two captions get
merged and the sense changes, a translation drifts onto the neighbouring
sentence, a name is corrected into a different name. The existing review runs
before all of that -- it clears out what recognition invented -- so nothing has
ever looked at what the editing itself produced.

This does, and it comes last, after the arithmetic repairs, so what it reads is
final and what it is asked about is only what could not be settled by counting.

The reply is a list of ids with a verdict, never rewritten text. A reviewer
that hands back prose is a reviewer that can quietly rewrite the transcript,
which is the failure this whole pipeline is built around avoiding: it may drop
a caption, restore a translation to a segment it clearly belongs to, or say
nothing. Anything it proposes is checked before it lands.
"""
from __future__ import annotations

import json
from typing import Any

MAX_FLAGGED = 0.08          # a review that condemns more than this misfired
BATCH = 40                  # captions per question, small enough to be read closely

PROMPT = """You are the final reviewer of a bilingual subtitle set that is
about to be burned into a video. The captions have already been proofread and
translated; your job is to catch what those steps broke.

Report only these, and only when you are confident:

  "meaning"      the translation says something the source line does not
  "misplaced"    the translation belongs to a different line than the one it is on
  "merged"       two utterances were joined into one caption and the sense is lost
  "invented"     the line refers to something never said in the surrounding text

Do NOT report: wording you would have phrased differently, missing punctuation,
formality, or a translation that is loose but faithful. Those are preferences,
and acting on them costs more than it gains.

Reply with JSON only:

  {"findings": [{"id": 12, "kind": "meaning", "why": "譯文說的是相反的意思"}]}

An empty list is the expected answer for a good transcript. Never return
corrected text; the pipeline does not accept it.
"""


def _payload(segments: list[dict[str, Any]]) -> str:
    return json.dumps(
        {"captions": [
            {"id": item["id"], "source": item.get("text", ""), "zh": item.get("zh", "")}
            for item in segments
        ]},
        ensure_ascii=False,
    )


def review_output(
    client: Any,
    segments: list[dict[str, Any]],
    language: str = "zh",
) -> tuple[list[dict[str, Any]], str]:
    """Read the finished captions. Returns them, plus a line about what was found.

    Nothing is deleted here. A finding marks the caption for the person who
    opens the run, because every kind it can report is a judgement about
    meaning -- and acting on a judgement without being able to check it is how
    a review pass starts damaging the thing it was meant to protect.
    """
    if not segments or client is None:
        return segments, ""
    if not any(item.get("zh") for item in segments) and not language.startswith("zh"):
        return segments, ""          # nothing bilingual to check

    findings: list[dict[str, Any]] = []
    for offset in range(0, len(segments), BATCH):
        batch = segments[offset:offset + BATCH]
        try:
            reply = client.chat_json(PROMPT, _payload(batch), timeout=180)
        except Exception as error:                                # noqa: BLE001
            return segments, f"審查失敗（{len(batch)} 段）：{error}"
        for item in reply.get("findings") or []:
            if not isinstance(item, dict) or "id" not in item:
                continue
            try:
                number = int(item["id"])
            except (TypeError, ValueError):
                continue
            if any(s["id"] == number for s in batch):
                findings.append({"id": number,
                                 "kind": str(item.get("kind", "meaning"))[:20],
                                 "why": str(item.get("why", ""))[:200]})

    if len(findings) > max(2, len(segments) * MAX_FLAGGED):
        # A reviewer condemning a tenth of the transcript is not reviewing.
        return segments, (f"審查標記了 {len(findings)} 段（超過 "
                          f"{MAX_FLAGGED:.0%}），視為誤判，全部忽略")

    flagged = {item["id"]: item for item in findings}
    marked = [{**item, "review": flagged[item["id"]]} if item["id"] in flagged else item
              for item in segments]
    if not findings:
        return marked, "審查沒有發現問題"
    lines = "；".join(f"#{f['id']} {f['kind']}" for f in findings[:6])
    return marked, f"審查標記 {len(findings)} 段待人確認：{lines}"
