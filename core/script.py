"""The script: what will be said, in order, with what on screen and where it
came from.

Everything else follows from this. The narration decides the length, the
length decides how many lines fit, and each line decides what has to be on
screen while it is spoken. Written the other way round -- pictures first, words
fitted afterwards -- you get a clip reel with captions, which is what the
reused-content rules exist to catch.

So a script is not prose. It is a list of lines, each one carrying its own
duration, its own picture, and the source of whatever fact it states. A line
with a claim and no source is a fault, and the page says so.

    90 seconds of Chinese narration is about 400 characters.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = ROOT / "scripts"
SAFE_NAME = re.compile(r"[\w一-鿿][\w一-鿿 -]{0,63}")

# Measured on news-paced Mandarin. Used to turn a script into seconds before
# anything is recorded, so "90 seconds" is arithmetic rather than a hope.
#
# These read from assets/rules.json rather than being written here, because
# every one of them was also written into a prompt in prose and the two copies
# drift apart without anything noticing.
from core import rules as rules_module

PER_SECOND = rules_module.at("pace.spoken_per_second", 4.5)
READ_PER_SECOND = rules_module.at("pace.read_per_second", 4.6)
LEAST_SECONDS = rules_module.at("pace.least_seconds", 1.9)
LIMIT = rules_module.at("length.limit_seconds", 90.0)


def spoken_length(text: str) -> int:
    """Characters that take time to say. Spaces and Western punctuation do not;
    a Chinese full stop does, because the reader pauses on it."""
    return len(re.sub(r"[\s -/:-@]", "", text or ""))


def line_seconds(line: dict[str, Any]) -> float:
    """How long this line takes. A stated duration wins -- some lines are held
    on a picture longer than they take to read."""
    given = line.get("seconds")
    if given:
        return float(given)
    return round(spoken_length(line.get("say", "")) / PER_SECOND, 2)


REAL_AIM = rules_module.at("borrowed.aim", 0.25)
REAL_MOST = rules_module.at("borrowed.most", 0.35)
CLIP_LEAST = rules_module.at("borrowed.clip_least", 0.5)
DRAWN = "自製"


def is_clip(line: dict[str, Any]) -> bool:
    """Whether this line runs a piece of the source video rather than a still.

    The first shorts were built entirely from cards and photographs -- five
    videos were downloaded per topic and used only as a place to take
    screenshots from. Nothing on screen ever moved. With no narration that is
    the whole of the medium thrown away: a still of a crowd cannot say that
    something is happening, and something happening is what holds anyone past
    the third second.

    So half the borrowed time has to move, and `measure` reports whether it
    does.
    """
    clip = line.get("clip")
    return bool(clip and clip.get("file"))


def is_real(show: str | None) -> bool:
    """Whether this shot is a photograph or footage rather than a card we drew.

    Told apart by what the shot says it is, and everything that is not drawn
    counts: a stock photograph, one from Commons, a frame lifted from the news.
    All three do the job a card cannot -- they look like the world -- and a
    video made only of cards is dull however good the cards are.

    Named for what it measures. It was called is_borrowed while the vocabulary
    was CNN and 原畫面; the vocabulary changed to 示意/真實/新聞畫格 and the
    check went on looking for words nobody was writing, so it reported no
    footage at all in a script full of it.
    """
    return bool(show) and not show.strip().startswith(DRAWN)


def gathered(script: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """What the topic knows about this script's pictures and footage.

    Here rather than in whoever happens to be calling. The credit line went
    missing from a finished film because this join lived in the web handler
    only, so the builder had no way to know which channel a file came from and
    passed an empty string; the check that would have caught it could not run
    either, for the same reason. A check that depends on the caller
    remembering to supply its evidence is a check that will not run on the day
    it matters.
    """
    from core import topic as topic_module
    try:
        pile = topic_module.load(script.get("topic", ""))
    except (ValueError, FileNotFoundError):
        return {}, {}
    return ({item["file"]: item for item in pile["sources"]["images"]
             if item.get("file")},
            {item["file"]: item for item in pile["sources"]["videos"]
             if item.get("file")})


# Every gate, in one place, with what it is actually testing.
#
# `kind` is the part worth writing down, because the gates are not all the
# same kind of check and their reliability differs accordingly:
#
#   frame     the thing the viewer will see. Nothing in between -- strongest
#   script    the script's own internal consistency; needs no other file
#   join      the seam between the script and the pile it was written from
#   declared  a claim the writer made. Cannot see the thing itself -- weakest
#   sequence  the film as an ordered run of shots rather than line by line
#
# The page at /gates reads this, so there is one list rather than one per
# reader. `blocks` says whether build() refuses; build() reads it too.
GATES = [
    ("shapeless",  "結構不對",           "script",   True,
     "起承轉合四個都要在、順序不能亂、轉要落在前 34%"),
    ("unsourced",  "陳述事實沒標來源",     "script",   False,
     "有 say 就要有 from。標「觀點」的不算 —— 那是我們的判斷，不是事實"),
    ("unpicked",   "沒有畫面",           "join",     True,
     "指的檔案要存在，而且要是這個題目收的。別題的照片檔案也存在"),
    ("unchecked",  "沒有人看過那張圖",     "declared", True,
     "seen 欄位。它測不到那張圖對不對，只測有沒有人聲稱看過"),
    ("undrawn",    "沒說卡片怎麼畫",       "script",   True,
     "show 寫「自製」就一定要有 card"),
    ("uncredited", "引用的畫面沒有出處",    "frame",    True,
     "借來的每一格都要能燒上「畫面來源：某家」"),
    ("samey",      "連續太多張長一樣",     "sequence", True,
     "同一種卡連續超過 3 張、同一色調超過 12 張。單張都好，排在一起是投影片"),
    ("unsigned",   "沒有結尾頁",          "frame",    True,
     "最後一句要是 outro：帶得走的一句話 + 回顧 + 頻道標記"),
    ("simplified", "簡體字",             "frame",    True,
     "say／show／note 三個欄位逐字判。OpenCC 兩次轉換交叉比對"),
    ("card_wrong", "卡片畫不出來",         "script",   True,
     "每種卡需要的欄位。bars 的長度要是數字，不能是「超過 1/3」"),
]

# One frame of slack on the length, and it is not a fudge: a film cannot be
# shorter than its own frames, so an overrun below that is arithmetic rather
# than length. It lives here because two readers needed it and only build()
# had it -- the page listing every script reported a shipped 90.01s film as
# too long while the builder had accepted it, which is how a gate loses its
# authority.
SLACK_SECONDS = round(1 / 30, 4)


def too_long(measured: dict[str, Any]) -> bool:
    """Whether this really runs over, as opposed to rounding over."""
    return measured.get("over", 0.0) > SLACK_SECONDS


# The three that are a number plus a verdict rather than a list of faults.
SUMS = [
    ("over",         "超過 90 秒",   "frame",
     "成片長度。上限在 rules.json 的 length.limit_seconds"),
    ("even",         "實拍比例與分布", "frame",
     "借來的畫面 ≤35%，而且三段都要有。一整團擠在一起看起來像別人的片"),
    ("still_enough", "會動的畫面夠",   "frame",
     "借來的時間至少一半要是會動的片段，不能全是靜照"),
]


def measure(script: dict[str, Any]) -> dict[str, Any]:
    """The script's own arithmetic: length, and whether it fits."""
    lines = script.get("lines") or []
    clock = 0.0
    laid = []
    for line in lines:
        span = line_seconds(line)
        laid.append({**line, "at": round(clock, 2), "seconds": round(span, 2),
                     "characters": spoken_length(line.get("say", ""))})
        clock += span
    said = sum(item["characters"] for item in laid)
    # A line that states a fact and names no source is the fault worth catching
    # early: by the time it is spoken aloud nobody checks it again. A line
    # marked 觀點 is ours and needs no source -- that is the difference between
    # an unsupported claim and an opinion, and the page should not confuse them.
    unsourced = [item["at"] for item in laid
                 if item.get("say") and not item.get("from")]
    opinion = sum(1 for item in laid if item.get("from") == "觀點")

    # Borrowed footage is what decides whether the video reads as ours, and it
    # has a shape as well as a size: 25% is fine spread across the running
    # time and looks like evidence; the same 25% in one lump looks like a clip
    # with commentary bolted on. So both are measured.
    lifted = [item for item in laid if is_real(item.get("show"))]
    borrowed = sum(item["seconds"] for item in lifted)
    thirds = [0, 0, 0]
    for item in lifted:
        where = min(2, int(item["at"] / max(clock, 1) * 3))
        thirds[where] += 1
    moving = sum(item["seconds"] for item in lifted if is_clip(item))
    most = rules_module.of(script, "borrowed.most", REAL_MOST)
    least = rules_module.of(script, "borrowed.clip_least", CLIP_LEAST)
    out = {"lines": laid, "seconds": round(clock, 2), "characters": said,
            "over": round(max(0.0, clock - LIMIT), 2), "unsourced": unsourced,
            "opinion": opinion,
            "borrowed": round(borrowed, 2),
            "borrowed_share": round(borrowed / clock * 100) if clock else 0,
            "spread": thirds,
            "even": all(thirds) and borrowed <= clock * most,
            "clip_seconds": round(moving, 2),
            "clip_share": round(moving / borrowed * 100) if borrowed else 0,
            "still_enough": borrowed > 0 and moving >= borrowed * least,
            "unpicked": missing_pictures(script),
            "unchecked": unchecked(script),
            "undrawn": undrawn(script),
            "uncredited": uncredited(script, *gathered(script)),
            "samey": samey(script),
            "unsigned": unsigned(script),
            "simplified": simplified(script),
            "card_wrong": card_wrong(script),
            "house": rules_module.house(script.get("format")).get("name", ""),
            "roles": roles_of(script),
            "borrowed_most": most,
            "shapeless": []}
    out["shapeless"] = structure(script, out)
    out["rights"] = rights(script, gathered(script)[0], out)
    return out


PER_ROW = rules_module.at("caption.per_row", 13)
MOST_ROWS = rules_module.at("caption.most_rows", 3)


def _chunks(text: str) -> list[str]:
    """The pieces a line may be broken between.

    A run of Latin letters or digits is one piece -- "AI" and "7.1%" read as
    words and splitting them is worse than a ragged edge. Punctuation joins
    the piece before it, so a row never opens with a comma or a closing
    bracket stranded on its own.
    """
    pieces: list[str] = []
    run = ""
    for ch in text:
        if ch.isascii() and (ch.isalnum() or ch in ".%$-"):
            run += ch
            continue
        if run:
            pieces.append(run)
            run = ""
        if ch in "，。、？！：；」』…％" and pieces:
            pieces[-1] += ch          # closers stay with what they close
        elif ch.strip():
            pieces.append(ch)
        elif pieces:
            pieces[-1] += ch          # a space belongs to the word before it
    if run:
        pieces.append(run)
    return pieces


def wrap(text: str, per: int | None = None, most: int | None = None) -> list[str]:
    """Break a caption into rows that fit the frame.

    Breaking only at punctuation is not enough, and the failure is invisible
    until it is burned in: a sentence whose first comma falls before the limit
    never breaks there, so the whole line runs on and is drawn off both edges
    of the frame. Width decides where a row ends; the chunks decide where it
    may end.
    """
    per = per or rules_module.at("caption.per_row", PER_ROW)
    most = most or rules_module.at("caption.most_rows", MOST_ROWS)
    rows: list[str] = []
    row = ""
    for piece in _chunks(text):
        if row and len(row) + len(piece) > per:
            rows.append(row)
            row = piece
        else:
            row += piece
        # A row that closes on punctuation is a good place to stop, so long as
        # it is not so short that the next one is left carrying everything.
        if row and row[-1] in "。？！" and len(row) >= per - 5:
            rows.append(row)
            row = ""
    if row:
        rows.append(row)
    return rows[:most] or [text[:per]]


def missing_pictures(script: dict[str, Any]) -> list[dict[str, Any]]:
    """Lines that call for a photograph and do not name one this topic gathered.

    A line used to say 真實：變電所 and the file was chosen days later, while
    building. That splits one decision in two: the writer never saw what was
    available, and whoever picked the file never knew why the line wanted it.
    So a line that is not drawn names its picture, and the page shows it.

    Existing on disk is not enough, and the difference matters more the less a
    person is involved. Three times I named a picture from memory and twice it
    belonged to a different topic -- files that exist, so this check passed
    them, and only the credit and the caption showed anything was wrong.
    Writing scripts in Python I can be handed a dictionary of this topic's
    pictures and be unable to mistype one; a model returning JSON writes the
    path itself, and would sail straight through. So the check is now that the
    file is one of this topic's own.
    """
    pictures, footage = gathered(script)
    lack = []
    for index, line in enumerate(script.get("lines") or [], start=1):
        if not is_real(line.get("show")):
            continue
        note = {"line": index, "say": line.get("say", ""),
                "show": line.get("show", "")}
        if is_clip(line):
            clip = line["clip"]
            if not (ROOT / clip["file"]).is_file():
                lack.append({**note, "why": f"找不到 {clip['file']}"})
            elif footage and clip["file"] not in footage:
                lack.append({**note, "why": f"{clip['file']} 不是這個題目的影片"})
            elif float(clip.get("end", 0)) <= float(clip.get("start", 0)):
                lack.append({**note, "why": "段落的起訖時間不對"})
            continue
        pic = line.get("pic")
        if not pic:
            lack.append({**note, "why": "沒指定圖片"})
        elif not (ROOT / pic).is_file():
            lack.append({**note, "why": f"找不到 {pic}"})
        elif pictures and pic not in pictures:
            lack.append({**note, "why": f"{pic} 不是這個題目收的圖"})
    return lack


def unchecked(script: dict[str, Any]) -> list[dict[str, Any]]:
    """Lines whose picture nobody has looked at.

    `missing_pictures` passes a line that names a file which exists. Both of
    those were true of a photograph labelled 示意：帳單特寫 that showed a fuse
    box on a wall: it was chosen from the search term `electricity bill`, and
    Pexels matches word by word, so `bill` found `billing` in a caption about
    an electricity meter. Nobody opened it.

    The program can check that a file exists. It cannot check that the picture
    is of the thing the line claims, and the page was showing those two states
    identically. So a picture carries `seen`, set by whoever looked -- a person
    now, a vision model once one is wired in -- and until then the line is
    listed here.
    """
    out = []
    for index, line in enumerate(script.get("lines") or [], start=1):
        if not is_real(line.get("show")):
            continue
        if not (line.get("pic") or is_clip(line)):
            continue                      # already reported as unpicked
        if not line.get("seen"):
            out.append({"line": index, "say": line.get("say", ""),
                        "show": line.get("show", ""),
                        "why": "還沒有人看過這張圖"})
    return out


# What we may do with each shot, which is not the same question as where it
# came from. Ordered by how much trouble it can cause.
FREE = "free"        # public domain or a licence asking for nothing
CREDIT = "credit"    # CC BY / BY-SA: the author has to appear on screen
CLAIMED = "claimed"  # somebody else's copyright, used as quotation
OURS = "ours"        # we drew it

RIGHTS = {OURS: "自製", FREE: "免標示", CREDIT: "要標示", CLAIMED: "有版權"}


def rights_of(line: dict[str, Any], source: dict[str, Any] | None) -> str:
    """Which of those four this line's picture is.

    A frame lifted from a broadcast is as much somebody else's copyright as
    the moving version of it -- freezing a picture does not make it ours -- so
    stills cut from the topic's own videos land in the same bucket as clips.
    That is easy to forget precisely because the file looks like a photograph
    by the time it reaches the script.
    """
    if not is_real(line.get("show")):
        return OURS
    if is_clip(line):
        return CLAIMED
    if not source:
        return CLAIMED                    # unknown provenance is not a defence
    if source.get("kind") == "frame":
        return CLAIMED
    return CREDIT if source.get("credit") else FREE


def rights(script: dict[str, Any], sources: dict[str, dict[str, Any]],
           measured: dict[str, Any] | None = None) -> dict[str, Any]:
    """How much of the running time we may do what with.

    The share that matters for a strike is not "borrowed" -- a stock
    photograph is borrowed and carries no risk at all. It is the share that
    somebody else owns, which on a video made of cards and quotations is a
    much smaller number and the only one worth watching.
    """
    measured = measured or measure(script)
    clock = measured["seconds"] or 1
    seconds = {key: 0.0 for key in RIGHTS}
    holders: dict[str, float] = {}
    for line in measured["lines"]:
        source = sources.get(line.get("pic") or "")
        kind = rights_of(line, source)
        seconds[kind] += line["seconds"]
        if kind == CLAIMED:
            who = (line.get("outlet") or (source or {}).get("outlet")
                   or line.get("from") or "?")
            holders[who] = round(holders.get(who, 0.0) + line["seconds"], 2)
    return {
        "seconds": {key: round(value, 2) for key, value in seconds.items()},
        "share": {key: round(value / clock * 100) for key, value in seconds.items()},
        "holders": sorted(({"who": who, "seconds": secs,
                            "share": round(secs / clock * 100)}
                           for who, secs in holders.items()),
                          key=lambda one: -one["seconds"]),
        "labels": RIGHTS,
        # A quotation defence gets harder the more of the film is quotation and
        # the longer any single quotation runs. Neither number is a legal test
        # -- there is no percentage that makes it safe -- but they are the two
        # a reviewer looks at first, so they are the two shown.
        "longest": round(max((line["seconds"] for line in measured["lines"]
                              if rights_of(line, sources.get(line.get("pic") or ""))
                              == CLAIMED), default=0.0), 2),
    }


def undrawn(script: dict[str, Any]) -> list[dict[str, Any]]:
    """Drawn lines that say what they want and do not say how to draw it.

    The counterpart of `unpicked`. Seven tenths of the running time is cards,
    and for a long time every one of them existed only as a sentence in a
    `show` field -- 自製：一條線分岔成兩條 -- to be drawn by hand, later, by
    somebody reading that sentence. Which is the same split that put a fuse
    box under a line about a bill: the person who wrote it never saw it, and
    the person who drew it never knew why the line wanted it.
    """
    lack = []
    for index, line in enumerate(script.get("lines") or [], start=1):
        if is_real(line.get("show")) or line.get("card"):
            continue
        lack.append({"line": index, "say": line.get("say", ""),
                     "show": line.get("show", ""), "why": "沒說這張卡怎麼畫"})
    return lack


_BOTH_FORMS = set(rules_module.at("language.both_forms", "吃台岩游里托"))


def _is_simplified(char: str) -> bool:
    """Whether one character is written the mainland way.

    Two questions, because either alone is wrong. `t2s` leaves it alone: a
    traditional character would have been converted, so this is not one.
    `s2t` changes it: there is a traditional form it is not using. 的 passes
    the first and fails the second, which is right -- it is simply shared.
    """
    from opencc import OpenCC                            # slow import, rare use
    global _T2S, _S2T
    try:
        _T2S
    except NameError:
        _T2S, _S2T = OpenCC("t2s"), OpenCC("s2t")
    if char in _BOTH_FORMS:
        return False
    return _T2S.convert(char) == char and _S2T.convert(char) != char


def simplified(script: dict[str, Any]) -> list[dict[str, Any]]:
    """Lines written in simplified characters.

    A gate rather than a line in the prompt, and it earned that the hard way:
    the first Qwen draft came back entirely in simplified, including the roles
    -- 疑点, 悬念 -- and `shapeless` caught it only by accident, because those
    happened not to be words the format knew. Any format that had listed them
    would have let a whole simplified script through.

    The prompt never asked for traditional. Nine gates and six thousand words
    of instruction, and the one thing every single caption depends on was in
    neither. So it is checked here, where forgetting to write it down cannot
    matter, and `{language.name}` fills the prompt from the same field.
    """
    wrong = []
    for index, line in enumerate(script.get("lines") or []):
        for field in ("say", "show", "note"):
            found = sorted({char for char in str(line.get(field) or "")
                            if _is_simplified(char)})
            if found:
                wrong.append({"line": index, "field": field,
                              "say": line.get("say", ""),
                              "chars": "".join(found),
                              "why": f"簡體字：{''.join(found)}"})
    return wrong


def tone_of_line(line: dict[str, Any]) -> str:
    """Which palette this shot is drawn in.

    One expression, because there were two. `samey` counted tone runs as
    `card.tone or line.tone`; the renderer read only `card.tone`. A script
    written cool → light → warm passed the gate as three sections and came out
    of the encoder as one flat blue, and nothing could have reported it: both
    halves were behaving exactly as written.

    A card may still name its own tone -- the ending sometimes wants to sit
    apart from the section it is in -- but the line is the default and the
    line is what the checks count.
    """
    card = line.get("card") or {}
    return str(card.get("tone") or line.get("tone") or "cool")


def samey(script: dict[str, Any]) -> list[dict[str, Any]]:
    """Runs of identical-looking shots.

    Found by laying the finished film out as one sheet, which is the only way
    it can be found: every one of those cards is fine on its own, and four of
    them in a row are a slideshow. The first ending ran six `word` cards
    together -- same size, same position, same ground -- and the eye has
    nothing to follow from one to the next.

    A run of borrowed shots is not counted. Photographs and clips differ from
    each other by being photographs of different things; two cards of the same
    kind differ only in their words.
    """
    most = rules_module.of(script, "cards.same_kind_run", 3)
    tone_most = rules_module.of(script, "cards.same_tone_run", 12)
    lines = script.get("lines") or []
    faults, run, tone_run = [], [], []

    def close(group: list[tuple[int, str]], limit: int, what: str) -> None:
        if len(group) > limit:
            faults.append({
                "line": group[0][0], "why": f"連續 {len(group)} 句都是"
                                            f"{what}「{group[0][1]}」，最多 {limit}",
                "lines": [index for index, _ in group]})

    for index, line in enumerate(lines, start=1):
        card = line.get("card") or {}
        kind = str(card.get("kind") or "") if not is_real(line.get("show")) else ""
        if kind and run and run[-1][1] == kind:
            run.append((index, kind))
        else:
            close(run, most, "卡片")
            run = [(index, kind)] if kind else []
        tone = tone_of_line(line)
        if tone_run and tone_run[-1][1] == tone:
            tone_run.append((index, tone))
        else:
            close(tone_run, tone_most, "色調")
            tone_run = [(index, tone)]
    close(run, most, "卡片")
    close(tone_run, tone_most, "色調")
    return faults


def unsigned(script: dict[str, Any]) -> list[dict[str, Any]]:
    """A film that stops instead of ending.

    The last frame is where attention is highest -- someone who has just
    followed an argument to its end is, for about half a second, looking
    straight at the thing that made them follow it. That half second is the
    only moment anybody subscribes, and it was being spent on another caption
    like all the others.

    So the last line is an `outro`: one sentence worth repeating to a friend,
    with the channel mark in the corner. Checked rather than remembered,
    because "don't forget the end card" is precisely the kind of rule that
    lives in a document and gets forgotten.
    """
    if not rules_module.at("ending.required", True):
        return []
    lines = script.get("lines") or []
    if not lines:
        return [{"why": "沒有句子"}]
    last = lines[-1]
    card = last.get("card") or {}
    if str(card.get("kind")) != "outro":
        return [{"line": len(lines), "say": last.get("say", ""),
                 "why": "最後一句要是 outro：一句帶得走的話，右下角放頻道標記"}]
    if not str(card.get("title") or "").strip():
        return [{"line": len(lines), "say": last.get("say", ""),
                 "why": "outro 沒寫那句帶得走的話"}]
    least, most = rules_module.of(script, "ending.points", [3, 4])
    points = [one for one in (card.get("points") or []) if str(one).strip()]
    if not least <= len(points) <= most:
        return [{"line": len(lines), "say": last.get("say", ""),
                 "why": f"outro 的摘要有 {len(points)} 條，要 {least}–{most} 條"}]
    return []


def out_of_order(given: list[str], roles: list[str] | None = None) -> str:
    """Where a sequence of 起承轉合 stops going forwards.

    Separate from `structure` because it answers a different question at a
    different moment: `structure` reports on a finished script, this one is
    asked before an edit is accepted. A story can be short of 承 while it is
    being written; it cannot have 合 in front of 轉 at any point, because that
    is not a shape anybody is working towards.
    """
    roles = roles or ROLES
    seen = [roles.index(role) for role in given if role in roles]
    for index in range(1, len(seen)):
        if seen[index] < seen[index - 1]:
            return f"「{roles[seen[index]]}」不能排在「{roles[seen[index - 1]]}」後面"
    return ""


def uncredited(script: dict[str, Any],
               sources: dict[str, dict[str, Any]] | None = None,
               footage: dict[str, dict[str, Any]] | None = None
               ) -> list[dict[str, Any]]:
    """Borrowed shots with nobody's name on them.

    This one had already been written down twice -- COLLECTING.md says the
    credit has to reach the screen, the prompt says it -- and the first cut of
    the film went out with none of its three quotations credited. `clip_cut`
    refuses to *crop* without a credit, and nothing was cropped, so the guard
    never fired; the join from a file to the broadcaster who shot it existed
    only in the web handler, so the builder passed an empty string and ffmpeg
    dutifully drew nothing. The channel logos survived, which is why it was not
    obvious.

    Taking someone's pictures and putting our own words on them without saying
    whose they are is misappropriation whatever the intent, so this is a gate
    and not a warning.
    """
    sources, footage = sources or {}, footage or {}
    lack = []
    for index, line in enumerate(script.get("lines") or [], start=1):
        if not is_real(line.get("show")):
            continue
        if is_clip(line):
            who = footage.get(line["clip"]["file"], {}).get("outlet", "")
            if not who:
                lack.append({"line": index, "say": line.get("say", ""),
                             "why": "這段影片查不到是哪一台的"})
            continue
        source = sources.get(line.get("pic") or "")
        if not source:
            continue                       # already reported as unpicked
        # A stock photograph asks for nothing; Commons and a lifted frame both
        # do, and both record what they ask for when they are fetched.
        if source.get("kind") in ("real", "frame") and not source.get("credit"):
            lack.append({"line": index, "say": line.get("say", ""),
                         "why": f"{source.get('outlet') or '這張圖'} 沒有出處"})
    return lack


ROLES = rules_module.at("structure.roles", ["起", "承", "轉", "合"])


def roles_of(script: dict[str, Any]) -> list[str]:
    """The role vocabulary this script is written in.

    An argument runs 起承轉合; a story runs 場景／事件／疑點／懸念. Both are
    checkable in exactly the same way -- all present, in order, each with
    enough lines -- and neither can be checked against the other's words.
    """
    return rules_module.of(script, "structure.roles", ROLES)


def structure(script: dict[str, Any],
              measured: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Whether the thing has a shape.

    "It should have a beginning, a development, a turn and a landing" cannot be
    checked, and saying it in a prompt is how it goes unread. What can be
    checked is whether the writer *said* which line is which -- so every line
    declares a role, and this checks the declaration: all four present, in
    order, and the turn inside the first third.

    That does not make the turn good. It makes the unanswerable question small:
    from "is this script well structured" down to "is this one turn a real
    reversal or just the same thing said again", which is what the second pass
    is for.
    """
    measured = measured or measure(script)
    roles = roles_of(script)
    lines = measured["lines"]
    faults = []
    given = [line.get("role") for line in lines]
    if any(role not in roles for role in given):
        return [{"why": "每一句都要標 " + "／".join(roles),
                 "lines": [index + 1 for index, role in enumerate(given)
                           if role not in roles]}]

    for role, least in (rules_module.of(script, "structure.least_per_role") or {}).items():
        got = given.count(role)
        if got < least:
            faults.append({"why": f"「{role}」只有 {got} 句，至少要 {least} 句"})

    order = [roles.index(role) for role in given]
    if order != sorted(order):
        back = next(i for i in range(1, len(order)) if order[i] < order[i - 1])
        faults.append({"why": f"順序亂了：第 {back + 1} 句的「{given[back]}」"
                              f"排在「{given[back - 1]}」後面"})

    clock = measured["seconds"] or 1
    # The third role is the turn in an argument and the doubts in a story;
    # a story sets `turn_before` to 0, which means it is not on a clock.
    turn = next((line for line in lines if line.get("role") == roles[2]), None)
    before = rules_module.of(script, "structure.turn_before", 0.34)
    if before and turn and turn["at"] > clock * before:
        faults.append({"why": f"「{roles[2]}」出現在 {turn['at']:.0f} 秒，"
                              f"超過前 {before * 100:.0f}%（{clock * before:.0f} 秒）"
                              f"，太晚了留不住人"})
    return faults


def too_long(script: dict[str, Any]) -> list[dict[str, Any]]:
    """Lines that will not fit, with what would be dropped."""
    over = []
    for index, line in enumerate(script.get("lines") or [], start=1):
        rows = wrap(line.get("say", ""))
        shown = "".join(rows)
        if shown != line.get("say", ""):
            over.append({"line": index, "say": line["say"],
                         "lost": line["say"][len(shown):]})
    return over


def path_for(name: str) -> Path:
    if not SAFE_NAME.fullmatch(name):
        raise ValueError("文案名稱只能用中英文、數字、底線、減號、空白")
    return SCRIPT_DIR / f"{name}.json"


def names() -> list[str]:
    if not SCRIPT_DIR.is_dir():
        return []
    return sorted(path.stem for path in SCRIPT_DIR.glob("*.json"))


def load(name: str) -> dict[str, Any]:
    path = path_for(name)
    if not path.is_file():
        raise FileNotFoundError(f"找不到文案 {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def save(name: str, script: dict[str, Any]) -> Path:
    path = path_for(name)
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(script, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def for_topic(topic: str) -> list[str]:
    """The scripts written from this topic.

    Derived rather than recorded. The topic file carried a `scripts` list that
    nothing wrote to, so four finished scripts existed on disk and the page
    showed none of them -- the link ran one way, from script to topic, and the
    other direction was a copy somebody was supposed to keep up to date.

    Two places holding one fact is the same fault that had a threshold written
    into both the code and a prompt. Reading it off the scripts themselves
    cannot drift.
    """
    found = []
    for name in names():
        try:
            if load(name).get("topic") == topic:
                found.append(name)
        except (ValueError, json.JSONDecodeError, FileNotFoundError):
            continue
    return found


def listing() -> list[dict[str, Any]]:
    found = []
    for name in names():
        try:
            script = load(name)
        except json.JSONDecodeError:
            continue
        sums = measure(script)
        found.append({
            "name": name,
            "topic": script.get("topic", ""),
            # Which shape it is. Listed, not only shown once a script is open:
            # the difference between an argument and a story is the first thing
            # anybody needs to know about one, and it decides what every other
            # number on the card means.
            "format": script.get("format", "argue"),
            "house": sums.get("house", ""),
            "seconds": sums["seconds"],
            "characters": sums["characters"],
            "over": sums["over"],
            "unsourced": len(sums["unsourced"]),
            "lines": len(sums["lines"]),
            "sources": sum(len(script.get("sources", {}).get(kind) or [])
                           for kind in ("video", "reports", "images")),
            "modified": int(path_for(name).stat().st_mtime),
        })
    return found


# What each kind of card needs before it can be drawn. Written here rather
# than discovered at encode time: a `bars` row whose value read 「超過 1/3」
# passed all eight gates, and the run died four minutes later inside
# ImageDraw with `could not convert string to float`. Every other picture
# fault in this project fails silently; this one fails loudly and very late,
# which is its own kind of expensive.
CARD_NEEDS: dict[str, dict[str, Any]] = {
    "bars":   {"rows": "list"},        # [[label, number], ...]
    "split":  {"branches": "list"},
    "stack":  {"items": "list"},
    "chain":  {"points": "list"},
    "queue":  {"count": "number"},
    # `part` is the one that becomes an angle. `value` is drawn as text --
    # 「3 分」 is a perfectly good dial label, and requiring a number here
    # would have refused to rebuild two films that have been fine for weeks.
    "clock":  {"part": "number", "value": "any"},
    "ring":   {"value": "any"},
    "swap":   {"was": "any", "now": "any"},
    "number": {"value": "any"},
    "outro":  {"points": "list", "title": "any"},
    "word":   {"title": "any"},
    "title":  {"title": "any"},
}


def _is_number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def card_wrong(script: dict[str, Any]) -> list[dict[str, Any]]:
    """Cards whose spec the renderer cannot draw.

    `undrawn` asks whether a card is there at all. This asks whether the one
    that is there can be drawn, which is a different question and the one that
    stopped a finished script four minutes into encoding.
    """
    faults = []
    for index, line in enumerate(script.get("lines") or []):
        spec = line.get("card")
        if not spec:
            continue
        kind = str(spec.get("kind") or "title")
        needs = CARD_NEEDS.get(kind)
        if needs is None:
            faults.append({"line": index, "say": line.get("say", ""),
                           "why": f"沒有這種卡：{kind}"})
            continue
        for field, shape in needs.items():
            value = spec.get(field)
            if value in (None, "", [], {}):
                faults.append({"line": index, "say": line.get("say", ""),
                               "why": f"{kind} 卡少了 {field}"})
            elif shape == "list" and not isinstance(value, list):
                faults.append({"line": index, "say": line.get("say", ""),
                               "why": f"{kind} 卡的 {field} 要是清單"})
            elif shape == "number" and not _is_number(value):
                faults.append({"line": index, "say": line.get("say", ""),
                               "why": f"{kind} 卡的 {field} 要是數字，寫的是「{value}」"})
        # A bar's length is its number. A row that says 「超過 1/3」 is a
        # sentence, and the renderer will try to make a width out of it.
        if kind == "bars":
            for row in spec.get("rows") or []:
                if not isinstance(row, (list, tuple)) or len(row) < 2:
                    faults.append({"line": index, "say": line.get("say", ""),
                                   "why": "bars 的每一列要是 [名稱, 數字]"})
                elif not _is_number(row[1]):
                    faults.append({"line": index, "say": line.get("say", ""),
                                   "why": f"bars 的長度要是數字，寫的是「{row[1]}」"})
    return faults
