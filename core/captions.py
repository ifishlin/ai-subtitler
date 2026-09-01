"""Reading a video's subtitles, so the pictures taken from it are chosen
rather than sampled.

Frames used to be cut at evenly spaced moments -- three or four across the
running time, whatever happened to be on screen. What that gives you is the
titles, the anchor's face, and two people sitting on stools; a broadcast cuts
every few seconds and the useful shot is almost never at 1/4, 2/4, 3/4.

The subtitles say what is being talked about at each second, and a news cut
generally shows what it is talking about. So the caption track is the index:
find the line that mentions the thing, take the picture there.

The text is not proof -- the words may be spoken over a reporter's face -- so
what this returns is a candidate, to be looked at before it is used.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

STAMP = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")
TAGS = re.compile(r"<[^>]+>")
# 一句話的結尾。字幕自己帶標點，但**換行不照標點走** —— 換行是照一行放得下
# 幾個字切的，所以一個句子橫跨三到十條 cue，而一條 cue 的兩端幾乎必定是半句。
ENDING = re.compile(r"(?<=[.!?])[\"')\]]?\s+")
# YouTube 自動字幕在同一行裡標出每個字幾秒開始：
#     Mr.<00:00:00.200><c> President,</c><00:00:00.440><c> you</c>
# 四十份字幕裡有二十七份是這種。剩下十三份是上傳者提供的乾淨字幕，只有 cue
# 的起訖，那十三份只能用估的。
INLINE = re.compile(r"<(\d{2}):(\d{2}):(\d{2}[.,]\d{3})>(?:<c>)?([^<]*)")
BARE = re.compile(r"[^a-z0-9']")


def _seconds(hours: str, minutes: str, secs: str, milli: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(secs) + int(milli) / 1000


def read(path: Path | str) -> list[dict[str, Any]]:
    """The cues of a VTT or SRT file, in order.

    YouTube's automatic captions repeat themselves: each cue carries the tail
    of the one before so the words appear to roll. Left alone that makes every
    search match three times over, so a cue keeps only what it adds.
    """
    path = Path(path)
    if not path.is_file():
        return []
    cues: list[dict[str, Any]] = []
    start = end = 0.0
    said: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        found = STAMP.search(raw)
        if found:
            if said:
                cues.append({"start": start, "end": end,
                             "text": " ".join(said).strip()})
            said = []
            start = _seconds(*found.group(1, 2, 3, 4))
            end = _seconds(*found.group(5, 6, 7, 8))
            continue
        # Broadcast captions arrive HTML-escaped -- `&nbsp;` between every
        # word on some channels, `&gt;&gt;` for a change of speaker. Left in,
        # they reach the page and any prompt built from it.
        line = html.unescape(TAGS.sub("", raw)).replace("\xa0", " ").strip()
        if line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if line and not line.isdigit():
            said.append(line)
    if said:
        cues.append({"start": start, "end": end, "text": " ".join(said).strip()})

    tidy: list[dict[str, Any]] = []
    for cue in cues:
        if not cue["text"]:
            continue
        if tidy and cue["text"] in tidy[-1]["text"]:
            tidy[-1]["end"] = cue["end"]      # a repeat, not a new line
            continue
        if tidy and tidy[-1]["text"] in cue["text"]:
            cue["text"] = cue["text"][len(tidy[-1]["text"]):].strip() or cue["text"]
        tidy.append(cue)
    return tidy


def word_times(path: Path | str | None) -> list[tuple[float, str]]:
    """每個字幾秒開始，如果這份字幕自己寫了的話。

    自動字幕寫了，上傳者提供的乾淨字幕沒有寫。回傳空的清單不是錯，是「這份
    要用估的」—— 兩種來源都要能走完，所以差別只在精確度，不在能不能做。
    """
    path = Path(path) if path else None
    if not path or not path.is_file():
        return []
    found: list[tuple[float, str]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        for hours, minutes, secs, word in INLINE.findall(raw):
            bare = BARE.sub("", html.unescape(word).lower())
            if bare:
                found.append((int(hours) * 3600 + int(minutes) * 60
                              + float(secs.replace(",", ".")), bare))
    found.sort(key=lambda one: one[0])
    return found


def _snap(exact: list[tuple[float, str]], guess: float, word: str,
          reach: float = 1.5) -> float:
    """把估出來的切點換成那個字真正的時間，找得到的話。

    只動兩端 —— 中間不必準。切點落在句子的邊界，也就是說話的停頓處，而估算
    的誤差（拿有時間戳的二十七份回頭比對兩萬八千個字，中位 0.13 秒）本來就
    小於那個停頓。
    """
    if not exact or not word:
        return guess
    best = None
    for at, said in exact:
        if at < guess - reach:
            continue
        if at > guess + reach:
            break
        if said == word and (best is None or abs(at - guess) < abs(best - guess)):
            best = at
    return guess if best is None else best


def sentences(cues: list[dict[str, Any]],
              track: Path | str | None = None) -> list[dict[str, Any]]:
    """整支字幕接成一條，再照句子切開。

    這一支是為了修一個藏了很久的錯：`passages()` 的說明寫著段落「start and
    end on a sentence boundary」，而它做的是整條整條長 cue。cue 的邊界不是
    句子的邊界 —— 一支影片只有一到三成的 cue 結束在句尾。實測安大略湖那題
    的四十段候選，**只有四段兩邊都是完整句子**，78% 的結尾切在句子中間，
    送給模型的逐字稿長成「order today renaming… It's part of his」。

    做法是把每個字元對到一個秒數，再用標點切。字元的秒數有兩種來源，而兩種
    都要能走：自動字幕寫了每個字的時間（`word_times`），乾淨字幕沒寫，就假設
    整條 cue 說話速度一致按字元比例推。兩端再用真值校正一次。
    """
    text = ""
    marks: list[float] = []
    for cue in cues:
        piece = cue["text"].strip()
        if not piece:
            continue
        if text:
            text += " "
            marks.append(marks[-1])
        span = max(cue["end"] - cue["start"], 0.01)
        marks.extend(cue["start"] + span * index / len(piece)
                     for index in range(len(piece)))
        text += piece
    if not text:
        return []
    tail = cues[-1]["end"]
    exact = word_times(track)

    spots, at = [], 0
    for piece in ENDING.split(text):
        spots.append((at, piece))
        at += len(piece)
        # `split` 吃掉了句號後面的空白，長度對不回去 —— 照原文往前走到下一個
        # 非空白，才是下一句真正的起點。少了這一段，時間會愈往後愈偏。
        while at < len(text) and text[at].isspace():
            at += 1
    found = []
    for index, (start, piece) in enumerate(spots):
        body = piece.strip()
        if not body:
            continue
        opens = BARE.sub("", body.split()[0].lower())
        head = _snap(exact, marks[min(start, len(marks) - 1)], opens)
        # 結尾就是下一句的開頭：中間那段是停頓，留給前面那一句才不會切掉尾音。
        if index + 1 < len(spots):
            after, nxt = spots[index + 1]
            words_after = nxt.strip().split()
            foot = _snap(exact, marks[min(after, len(marks) - 1)],
                         BARE.sub("", words_after[0].lower()) if words_after else "")
        else:
            foot = tail
        if foot > head:
            found.append({"start": round(head, 2), "end": round(foot, 2),
                          "text": body})
    return found


def moments(cues: list[dict[str, Any]], words: list[str],
            most: int = 6, apart: float = 12.0) -> list[dict[str, Any]]:
    """Where in this video the words are being said.

    Returns the middle of each matching cue rather than its start: a cut
    usually lands on the first syllable of the sentence that describes it, and
    the shot that illustrates it is a beat later.

    Matches are kept `apart` seconds from each other. Broadcast repeats its
    key phrase several times in one breath, and three frames from one sentence
    are three copies of one picture.
    """
    wanted = [word.lower() for word in words if word and len(word) > 2]
    if not wanted:
        return []
    found: list[dict[str, Any]] = []
    for cue in cues:
        text = cue["text"].lower()
        hits = [word for word in wanted if word in text]
        if not hits:
            continue
        middle = round((cue["start"] + cue["end"]) / 2, 2)
        if found and middle - found[-1]["at"] < apart:
            # the same breath: keep whichever cue matched more of the words
            if len(hits) > len(found[-1]["hits"]):
                found[-1] = {"at": middle, "said": cue["text"], "hits": hits}
            continue
        found.append({"at": middle, "said": cue["text"], "hits": hits})
    found.sort(key=lambda one: (-len(one["hits"]), one["at"]))
    return found[:most]


def passages(cues: list[dict[str, Any]], words: list[str],
             want: float | None = None, most: int = 4,
             track: Path | str | None = None) -> list[dict[str, Any]]:
    """Stretches worth cutting as clips rather than stills.

    A clip has to start and end on a sentence boundary, or the words it
    carries open and close in the middle of a thought. This used to grow by
    whole *cues*, which is not the same thing and reads as if it were: a cue
    is as long as a line of text fits, so its edges fall wherever the
    line-breaking happened to land. Ten per cent of the passages came out
    whole.

    Now it grows by whole sentences, from `sentences()`. The bounds are a
    range rather than a floor: a floor alone produced a twenty-seven second
    passage, because one caption ran that long without a full stop, and
    nothing downstream would have refused it -- `fasten()` takes the passage's
    own length as the shot's length, so a single pick would have eaten a third
    of the film.
    """
    from core import rules as rules_module
    least, most_seconds = rules_module.at("collect.passage_seconds", [4.0, 12.0])
    want = float(want or least)
    spans = sentences(cues, track)
    if not spans:
        return []
    hits = moments(cues, words, most=most * 2, apart=want * 2)
    out: list[dict[str, Any]] = []
    taken: set[tuple[int, int]] = set()
    for hit in hits:
        first = next((i for i, one in enumerate(spans)
                      if one["start"] <= hit["at"] <= one["end"]), None)
        if first is None:
            continue
        last = first
        while spans[last]["end"] - spans[first]["start"] < want \
                and last + 1 < len(spans):
            if spans[last + 1]["end"] - spans[first]["start"] > most_seconds:
                break
            last += 1
        span = spans[last]["end"] - spans[first]["start"]
        # 太短是「這一句就這麼短，而且再加一句就超過上限」；太長是「這一段
        # 字幕整整二十幾秒沒有句號」。兩種都不是可以剪的東西，丟掉比硬剪好 ——
        # 硬剪就是切回半句，而那正是這一支要修的問題。
        #
        # 下限就是下限，不是下限的六成。本來寫 `want * 0.6`，於是 rules.json
        # 上寫著 4 秒而實際上 2.6 秒的段落照樣送出去 —— 一個沒有人會發現的
        # 謊，因為兩邊都「照寫的做」。改成照下限判只少了九段（三百零一→
        # 兩百九十二），而最後本來就只取四十段。
        if span < want or span > most_seconds:
            continue
        if (first, last) in taken:
            continue          # 兩個命中落在同一句，那是同一段
        taken.add((first, last))
        said = " ".join(one["text"] for one in spans[first:last + 1])
        low = said.lower()
        out.append({"start": round(spans[first]["start"], 2),
                    "end": round(spans[last]["end"], 2),
                    "seconds": round(span, 2),
                    "said": said,
                    # 整段涵蓋到的關鍵詞，不只命中的那條 cue 的 —— 段落現在
                    # 可能橫跨好幾條，而看的人要知道「為什麼是這一段」。
                    "hits": sorted({word for word in hit["hits"]}
                                   | {word.lower() for word in words
                                      if len(word) > 2 and word.lower() in low}),
                    # 哪一秒命中而被選上的。排序看的是命中幾個關鍵詞，
                    # 不是哪一個，所以那個「幾個」也要留著。
                    "at": hit["at"], "matched": sorted(hit["hits"])})
        if len(out) >= most:
            break
    return out
