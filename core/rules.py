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
import re
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


def _names(house_of: str | None = None) -> dict[str, Any]:
    """一份 prompt 裡的 `{名字}` 可以用哪些，以及它們今天的值。

    `fill()` 和 `unfilled()` 都問這裡。本來各自組一份 —— 內容一樣，所以看起來
    沒問題，直到有人加了一個算出來的名字：`fill()` 認得，`unfilled()` 不認得，
    於是「這份 prompt 有錯」而它其實填得好好的。同一個事實兩份，第三次。
    """
    known = _flatten(rules())
    if house_of:
        known.update(_flatten(house(house_of)))
    known.update({f"theme.{key}": value for key, value in _flatten(theme()).items()})
    known.update(_derived(known))
    return known


def _derived(known: dict[str, Any]) -> dict[str, Any]:
    """Numbers that are two other numbers multiplied.

    `visual.md` told the model「影片段落 2 到 3 段，每段 4 到 6 秒」—— 大約
    十八秒，而兩條門相乘要的是二十三秒。照那句話寫必定被 `still_enough`
    退回，而那句話從來沒有人算過：它是手寫的散文，寫的時候 `borrowed.least`
    那條下限還不存在。

    所以這裡算，不存進 rules.json。存一份就是第二份，而第二份會跟門的算法
    分家 —— 這個專案已經有過一次（網頁把三分之一實拍的文案報成「自製 100%」）。
    """
    out: dict[str, Any] = {}
    least = known.get("borrowed.least")
    clip_least = known.get("borrowed.clip_least")
    limit = known.get("length.limit_seconds")
    if isinstance(least, (int, float)) and isinstance(clip_least, (int, float)):
        # 實拍要佔一半，其中一半要會動 —— 影片段落佔總長的下限就是這兩個相乘。
        out["borrowed.clip_share"] = least * clip_least
        if isinstance(limit, (int, float)):
            out["borrowed.clip_seconds"] = round(limit * least * clip_least, 1)
    return out


NAMED = re.compile(r"\{([^{}\s:]+)(?::([a-z]+))?\}")


def fill(text: str, house_of: str | None = None) -> str:
    """Put the current numbers into a prompt.

    A prompt says `每句 {caption.per_row} 個中文字以內`, and this puts today's
    figure in on the way out. Prose that quotes a number is prose that goes
    stale; prose that names one cannot.

    `house_of` names a format, whose values win over the shared ones -- the
    story prompt asks for `{structure.least_per_role.疑點}`, which only exists
    in that format.
    """
    known = _names(house_of)
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


def unfilled(text: str, house_of: str | None = None) -> list[str]:
    """Names a prompt asks for that the rule files do not have. Checked when a
    prompt is loaded, so a typo is found at that moment rather than reaching a
    model as a literal brace."""
    known = set(_names(house_of))
    # Any name, not only ASCII ones. The pattern used to be [a-z_.]+, so
    # `{structure.least_per_role.疑點}` matched nothing: fill left it alone and
    # unfilled reported the prompt clean, and the literal braces would have
    # reached the model. A checker that cannot see a whole class of mistake
    # reports every one of them as fine.
    return [name for name, _ in NAMED.findall(text) if name not in known]


FORMATS = ROOT / "assets" / "formats"
FALLBACK = "argue"


def formats() -> dict[str, dict[str, Any]]:
    """The house styles a script can be written in.

    A short is not one shape. "Everyone argues about A and nobody asks B" wants
    a reversal in the first third and a conclusion that lands on the viewer's
    week; a theft wants to put you at the scene and leave you puzzled. Written
    to one template, the Messina script spent its first third compressing
    exposition -- the time, the crowd, the police, the closed roads -- to reach
    a turn on schedule, and those were the pictures worth dwelling on.

    So the thresholds that differ by shape live in a format, and the ones that
    do not -- caption width, reading pace, the length ceiling -- stay in
    rules.json, where there is one of each.
    """
    found = {}
    for path in sorted(FORMATS.glob("*.json")) if FORMATS.is_dir() else []:
        try:
            found[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
    return found


SAFE_KEY = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def save_house(key: str, spec: dict[str, Any]) -> Path:
    """Write a house style.

    Formats were two files I wrote by hand, which makes "what shapes exist"
    a thing only somebody editing the repository can change. They are data --
    a name, a role vocabulary, a few thresholds and the reason each one is
    what it is -- and the reason is the part worth keeping: `why` is the only
    field that survives being read six months later.
    """
    if not SAFE_KEY.fullmatch(key):
        raise ValueError("片型代號只能用小寫英數、底線、減號，開頭是英文字母")
    if not str(spec.get("name") or "").strip():
        raise ValueError("片型要有名字")
    roles = [str(one).strip() for one in (spec.get("structure") or {}).get("roles") or []]
    if len(roles) < 3 or len(set(roles)) != len(roles):
        raise ValueError("角色至少三個，而且不能重複")
    FORMATS.mkdir(parents=True, exist_ok=True)
    path = FORMATS / f"{key}.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    _cache.pop(str(path), None)
    return path


def drop_house(key: str) -> bool:
    """Remove one. The fallback shape cannot go -- a script with no format is
    an argument, and nothing would be left to fall back to."""
    if key == FALLBACK:
        raise ValueError(f"{FALLBACK} 是預設片型，不能刪")
    path = FORMATS / f"{key}.json"
    if not path.is_file():
        return False
    # 片型是設計出來的，重跑補不回來 —— 所以移到 trash，不真刪。
    from core import bin as bin_module
    bin_module.toss([path], f"刪掉片型 {key}")
    _cache.pop(str(path), None)
    return True


def used_by(key: str) -> list[str]:
    """Scripts written in this shape. Asked before deleting one, because
    removing a format silently turns every script written in it into an
    argument with unreadable roles."""
    from core import script as script_module
    out = []
    for name in script_module.names():
        try:
            if script_module.load(name).get("format") == key:
                out.append(name)
        except Exception:                                         # noqa: BLE001
            continue
    return out


def house(which: str | None = None) -> dict[str, Any]:
    """One house style, falling back to the argument shape.

    Falling back rather than failing: a script written before formats existed
    has no `format` field and is an argument, which is what every one of them
    was.
    """
    known = formats()
    return known.get(str(which or FALLBACK)) or known.get(FALLBACK) or {}


def of(script: dict[str, Any], path: str, fallback: Any = None) -> Any:
    """A threshold for this script, from its own house style first.

    `of(script, "borrowed.most")` gives 0.35 to an argument and 0.5 to a story,
    and falls through to rules.json for anything a format does not override --
    so a format says only what makes it different, and the rest cannot drift
    apart from it.
    """
    where: Any = house(script.get("format"))
    for step in path.split("."):
        if not isinstance(where, dict) or step not in where:
            return at(path, fallback)
        where = where[step]
    return where
