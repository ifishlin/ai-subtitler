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


def read(name: str) -> dict[str, Any]:
    """Ask, and give back what came back.

    Nothing is configured yet, so this says so rather than pretending: the
    model is not connected, and the four questions are being answered by a
    person reading them. Saying which is which matters more than the answer --
    a review nobody ran should not look like a review that passed.
    """
    from core import settings as settings_module
    config = settings_module.load()
    spec = config.get("llm", {}).get("claude", {})
    try:
        key = settings_module.secret(spec, "claude")
    except RuntimeError as error:
        return {"asked": False, "why": str(error), "prompt": as_prompt(name)}

    import anthropic
    client = anthropic.Anthropic(api_key=key)
    said = client.messages.create(
        model=spec.get("model", "claude-opus-5"),
        max_tokens=2000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": as_prompt(name)}],
    )
    text = "".join(part.text for part in said.content
                   if getattr(part, "type", "") == "text")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return {"asked": True, "verdict": "unreadable", "said": text[:2000]}
    return {"asked": True, **json.loads(text[start:end + 1])}
