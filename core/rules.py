"""The numbers, in one place.

Every threshold in this project was written down twice: once as a constant in
Python, where the check reads it, and once in prose in a prompt, where the
writer reads it. Two copies of one number drift, and the drift is silent --
`is_real` went on looking for a vocabulary nobody was writing any more and
reported a script that was a third real footage as entirely drawn.

So the numbers live in assets/rules.json, the look in assets/theme.json, and
both the checks and the prompts read from here. `fill` puts them into a prompt
by name, so a sentence in a prompt cannot quote a stale figure: it either uses
the current one or fails loudly on a name that does not exist.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "assets" / "rules.json"
THEME = ROOT / "assets" / "theme.json"

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _read(path: Path) -> dict[str, Any]:
    """Reread when the file changes, so editing a threshold takes effect
    without restarting the server -- which is the point of it being a file."""
    stamp = path.stat().st_mtime if path.is_file() else 0.0
    known = _cache.get(str(path))
    if known and known[0] == stamp:
        return known[1]
    found = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    _cache[str(path)] = (stamp, found)
    return found


def rules() -> dict[str, Any]:
    return _read(RULES)


def theme() -> dict[str, Any]:
    return _read(THEME)


def at(path: str, fallback: Any = None) -> Any:
    """One value, by dotted path: `at("caption.per_row")`.

    A missing name returns the fallback rather than raising, because a rule
    file that has lost a key should degrade to the built-in default and say so
    in the logs, not stop a render halfway.
    """
    where: Any = rules()
    for step in path.split("."):
        if not isinstance(where, dict) or step not in where:
            return fallback
        where = where[step]
    return where


def look(path: str, fallback: Any = None) -> Any:
    """The same, for the theme."""
    where: Any = theme()
    for step in path.split("."):
        if not isinstance(where, dict) or step not in where:
            return fallback
        where = where[step]
    return where


def _flatten(source: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in source.items():
        if key in ("note", "why"):
            continue
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_flatten(value, f"{name}."))
        else:
            out[name] = value
    return out


def fill(text: str) -> str:
    """Put the current numbers into a prompt.

    A prompt says `每句 {caption.per_row} 個中文字以內`, and this puts today's
    figure in on the way out. Prose that quotes a number is prose that goes
    stale; prose that names one cannot.
    """
    known = _flatten(rules())
    known.update({f"theme.{key}": value for key, value in _flatten(theme()).items()})
    for name, value in known.items():
        for how, shown in _shapes(value).items():
            text = text.replace("{" + name + (f":{how}" if how else "") + "}",
                                shown)
    return text


def _shapes(value: Any) -> dict[str, str]:
    """The ways one value may be written into a sentence.

    A share stored as 0.34 belongs in prose as "34%", and a pair stored as
    [18, 22] belongs as "18–22". Writing those out by hand in the prompt is
    how the second copy of a number gets made, so the prompt asks for a shape
    -- `{structure.turn_before:pct}` -- and the shape is produced here.
    """
    plain = (f"{value:g}" if isinstance(value, float)
             else "–".join(str(one) for one in value)
             if isinstance(value, list) else str(value))
    out = {"": plain, "range": plain}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        out["pct"] = f"{value * 100:g}%"
    return out


def unfilled(text: str) -> list[str]:
    """Names a prompt asks for that the rule files do not have. Checked when a
    prompt is loaded, so a typo is found at that moment rather than reaching a
    model as a literal brace."""
    import re
    known = set(_flatten(rules())) | {
        f"theme.{key}" for key in _flatten(theme())}
    return [name for name in re.findall(r"\{([a-z_.]+)(?::[a-z]+)?\}", text)
            if name not in known]
