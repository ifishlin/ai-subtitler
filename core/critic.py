"""The second pass.

Some rules cannot be checked by a program and are not reliably followed by
asking. "It must have a turn" is one: `structure` can prove a line is labelled
轉 and sits in the first third, and cannot tell whether anything was actually
reversed.

What is left goes to a separate call. Separate is the whole point -- the pass
that wrote the script had a writing task in hand and its attention was on
whether the sentences flowed; a reviewer has one question and nothing else to
do. Asking the same generation to check itself is asking someone to proofread
while still typing.

This narrows what nobody can check from "is the script any good" to four
questions, and the last of those is the person watching it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core import brief as brief_module
from core import rules as rules_module
from core import script as script_module

ROOT = Path(__file__).resolve().parent.parent


def as_prompt(name: str) -> str:
    """What the reviewer is asked, with the script and its material under it."""
    found = script_module.load(name)
    measured = script_module.measure(found)
    body = (ROOT / "assets" / "prompts" / "review.md").read_text(encoding="utf-8")

    lines = ["", "---", "", "# 要審的文案", f"觀點：{found.get('view', '')}",
             f"說給誰聽：{found.get('for', '')}", ""]
    for index, line in enumerate(measured["lines"], start=1):
        lines.append(f"{index:2}  [{line.get('role', '?')}] "
                     f"{line.get('say', '')}　／{line.get('from', '')}")
    lines += ["", "# 程式已經算過的（不用再看）",
              f"長度 {measured['seconds']}s　實拍 {measured['borrowed_share']}%　"
              f"會動 {measured['clip_share']}%　沒出處 {len(measured['unsourced'])}",
              "", "---", "", brief_module.as_text(found.get("topic", ""))]
    return rules_module.fill(body) + "\n".join(lines)


def rounds() -> int:
    """How many times the writer may be asked to fix things.

    One, then stop. Most of what a review returns is checkable -- line 14
    cites nothing -- and one pass settles that. A second failure usually is
    not a rewriting problem: it means the topic has no view in it.

    Not unbounded, for two reasons this project has evidence for. A reviewer
    is wrong sometimes: `unindexed` caught the BBC piece about tigers and also
    flagged an AP piece squarely on topic, and rewriting to satisfy a mistaken
    verdict is worse than not reviewing. And the writer and the reviewer are
    two independent judgements, so past the second round they tend to oscillate
    between versions that are both fine rather than converge on a better one.
    """
    from core import rules as rules_module
    return int(rules_module.at("review.rounds", 1))


def settle(name: str, rewrite=None) -> dict[str, Any]:
    """Review, allow one fix, review again, then hand it over.

    `rewrite` is whatever continues the writing conversation -- the writer
    still has the material and its own draft in context, so a fix costs a few
    hundred tokens rather than another copy of everything. The reviewer is a
    fresh call each time on purpose: it must not know who wrote this.

    Whatever comes out, the last word is the person watching. A verdict is
    reported, never applied silently.
    """
    history = []
    for turn in range(rounds() + 1):
        said = read(name)
        history.append({"round": turn, **said})
        if not said.get("asked"):
            return {"state": "not_asked", "rounds": history}
        if said.get("verdict") == "pass":
            return {"state": "passed", "rounds": history}
        if turn >= rounds() or rewrite is None:
            return {"state": "needs_you", "rounds": history}
        rewrite(name, said)
    return {"state": "needs_you", "rounds": history}


def read(name: str) -> dict[str, Any]:
    """Ask, and give back what came back.

    Through the same call every other step uses, so the reviewer runs on
    whatever is configured -- which today is a model on cuba001 and costs
    nothing. It was wired to the Anthropic client and to nothing else, so this
    round had never once executed: the code path was finished and the only
    thing that could reach it was a key nobody had bought. A step that cannot
    run is not a step.

    A fresh call every time, on purpose -- the reviewer must not know who
    wrote this. And when it cannot be asked it says so, because a review
    nobody ran must not look like a review that passed.
    """
    from core import writer as writer_module
    try:
        text, took = writer_module.ask(as_prompt(name), None)
    except Exception as error:                                    # noqa: BLE001
        return {"asked": False, "why": f"問不到模型：{error}",
                "prompt": as_prompt(name)}
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return {"asked": True, "verdict": "unreadable", "said": text[:2000]}
    try:
        said = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"asked": True, "verdict": "unreadable", "said": text[:2000]}
    # An answer copied out of the prompt's own example is not an answer. A 30B
    # model handed `{"keep": [1, 4, 7]}` as an illustration returned exactly
    # that for thirty-six headlines, and here the example's reasons are about
    # a different topic entirely -- so if one comes back verbatim, the model
    # read the format and not the script.
    asked_for = as_prompt(name)
    copied = [part.get("why") for part in said.values()
              if isinstance(part, dict) and part.get("why")
              and part["why"] in asked_for]
    if copied:
        return {"asked": True, "verdict": "unreadable",
                "said": "照抄了 prompt 裡的範例：" + "、".join(copied[:3])}
    return {"asked": True, "took": round(took, 1), **said}
