"""Pull a YouTube channel's captions, not its videos, for corpus research.

This is not part of the production pipeline -- it does not touch `assets/`,
`core/topic.py`'s topics, or anything a script gets built from. It exists to
build a text corpus (`corpus/`) for structural analysis of *other* people's
shows: how a piece of long-form commentary or an existing YouTube channel
paces itself, so that work stays clearly separate from this project's own
news-sourcing (`core/topic.py`'s `bring_in()` / `hunt()`), even though the
underlying yt-dlp calls look similar.

Only caption/subtitle text is ever written to disk -- `--skip-download` is
always passed, so no video file is fetched. That mirrors how `bring_in()`
already treats a news clip's captions: read for what was said, not kept as
video.

Two lessons this project already paid for, both still true here:

  * One subtitle language per call. Requesting two at once (`--sub-langs
    en,zh-Hant`, or a glob like `en.*`) can pull in every auto-translated
    variant YouTube offers in one response, and a long burst of those in a
    row is what actually triggers the 429 -- not the request rate by itself.
    See docs/MISTAKES.md and `bring_in_why()`'s own comment on this.

  * A channel's plain `/videos` tab pages by *continuation token*, and deep
    enough into a long-running channel's history that token starts coming
    back `HTTP 500`/429 (page ~79 in testing here, channel-dependent). The
    channel's *uploads playlist* (`list=UU` + the channel id with `UC`
    dropped) is the same videos through the older, sturdier playlist
    paginator and did not show the same breakage -- so enumeration always
    goes through that rather than `/videos` directly.

Usage:

    python tools/fetch_channel_captions.py \\
        --channel @LastWeekTonight --out corpus/late-night/last-week-tonight \\
        --duration-min 900 --duration-max 2700 --sample 30 --label "Last Week Tonight"

    python tools/fetch_channel_captions.py \\
        --channel @TheDailyShow --out corpus/late-night/daily-show-noah \\
        --date-after 20150928 --date-before 20221231 --sample 30 \\
        --label "The Daily Show (Trevor Noah)"

    python tools/fetch_channel_captions.py \\
        --channel UCTZ4SJfKpSn5FixKQnOaj0A --out "corpus/曾秋教室/三年三班" \\
        --sample all --label "曾秋教室三年三班"

Resumable: a video already listed in the output manifest with a caption file
that still exists on disk is skipped on a re-run, so an interrupted run (rate
limit, network drop, Ctrl-C) only costs the time between the last completed
video and the interruption, not the whole channel again.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
YTDLP = str(ROOT / ".venv" / "bin" / "yt-dlp")
if not Path(YTDLP).is_file():
    YTDLP = "yt-dlp"          # fall back to PATH outside this project's venv


def run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([YTDLP, *args], capture_output=True, text=True,
                          timeout=timeout)


def is_rate_limited(proc: subprocess.CompletedProcess) -> bool:
    said = (proc.stderr or "") + (proc.stdout or "")
    return "429" in said or "Too Many Requests" in said


def uploads_playlist_url(channel: str) -> str:
    """The sturdier way to enumerate a channel: its own uploads playlist.

    `channel` may already be a playlist/watch URL (passed through as-is), a
    bare channel id (`UC...`), an `@handle`, or a full channel URL. A handle
    or vanity URL has to be resolved to a channel id first -- the id is what
    the `UC` -> `UU` swap needs -- which costs one light lookup call.
    """
    if "list=" in channel or "/playlist" in channel:
        return channel
    if channel.startswith("UC") and len(channel) == 24 and "/" not in channel:
        cid = channel
    else:
        # The bare channel URL (no /videos) dumped as one JSON object carries
        # channel_id at the top level. Once a /videos or /playlists tab is
        # appended, entries become individual videos/playlists instead, and
        # none of those carry the parent channel_id -- `--print` on that
        # form reliably comes back "NA", which cost a round trip to find out.
        url = channel if channel.startswith("http") else f"https://www.youtube.com/{channel.lstrip('/')}"
        proc = run([url, "--flat-playlist", "--playlist-end", "1",
                   "--dump-single-json", "--no-warnings"])
        cid = ""
        try:
            cid = json.loads(proc.stdout).get("channel_id") or ""
        except (json.JSONDecodeError, AttributeError):
            pass
        if not cid:
            raise SystemExit(f"沒辦法從 {channel!r} 找到 channel_id：\n{proc.stderr[-500:]}")
    return f"https://www.youtube.com/playlist?list=UU{cid[2:]}"


def search_url(channel: str, query: str) -> str:
    """This channel's own search tab, for pulling out one host's segments.

    `The Daily Show` has had rotating hosts since 2024 -- Stewart on Mondays,
    a different correspondent most other days -- so a plain date-range filter
    over the channel's uploads catches all of them, not just his. Its
    search-within-channel tab (the same page as the "Search channel" box on a
    channel's videos tab) doing the narrowing instead means the enumeration
    already only contains what was asked for.
    """
    import urllib.parse
    handle = channel if channel.startswith("http") else f"https://www.youtube.com/{channel.lstrip('/')}"
    return handle.rstrip("/") + "/search?query=" + urllib.parse.quote(query)


def enumerate_videos(playlist_url: str, cache_path: Path, start_at: int = 1,
                     say=print) -> list[dict[str, Any]]:
    """Every video this channel's uploads playlist knows about.

    One page at a time via `--playlist-start`/`--playlist-end` rather than a
    single unbounded call -- a single call over a 9,000-video channel is one
    long-held connection with nothing to show if it dies partway; paging
    means a break only loses the page in flight, and progress can be shown.
    `youtubetab:approximate_date` gives a day-level upload date for free
    (no extra request per video), which is all era-bucketing needs.

    This pagination has its own, separate 429 wall from downloading a single
    video's info or captions -- a long-running channel (~9,000 uploads, seen
    testing against this project's Job B channels) reliably gets throttled a
    few pages in, well before a whole history is walked. So progress is
    checkpointed to `cache_path` after every page, and a rerun against the
    same `--out` resumes from the last completed page instead of page 1 --
    each run only has to survive until the *next* wall, and eventually the
    whole history accumulates across a few runs rather than never finishing
    in one.
    """
    cached: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8")) \
        if cache_path.is_file() else {"next_start": start_at, "videos": []}
    videos: list[dict[str, Any]] = cached["videos"]
    seen_ids = {v["id"] for v in videos}
    page = 200
    start = cached["next_start"]
    if start > 1:
        say(f"  接續上次進度，從 {start} 開始（已經有 {len(videos)} 支）")
    misses = 0
    stall = 0
    while misses < 2:                      # two consecutive empty pages: done
        end = start + page - 1
        proc = run([playlist_url, "--flat-playlist", "--no-warnings",
                   "--playlist-start", str(start), "--playlist-end", str(end),
                   "--extractor-args", "youtubetab:approximate_date",
                   "--dump-json"], timeout=180)
        if is_rate_limited(proc):
            stall += 1
            if stall > 2:
                # In testing here, this pagination wall did not clear within
                # a 630-second, six-step backoff -- a long wait bought
                # nothing, it just spent ten minutes finding out slowly what
                # two quick tries already show. So this gives up fast instead
                # and leaves the real fix to the caller: a fresh *process*
                # against the same --out, later, resuming from the
                # checkpoint, did get further. Looping that restart from
                # outside is cheaper than looping the wait from in here.
                say(f"  ⚠ 429，{stall} 次都沒解除，停在 {start}-{end}，"
                    f"先用已經列到的 {len(videos)} 支；重開一個新的 process 對同一個 "
                    f"--out 續跑，通常會比在同一個 process 裡等更快過關")
                break
            wait = 8 * stall
            say(f"  429，等 {wait} 秒再試一次（{start}-{end}，第 {stall} 次）")
            time.sleep(wait)
            continue
        stall = 0
        rows = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        if not rows:
            misses += 1
        else:
            misses = 0
            for row in rows:
                vid = row.get("id")
                if vid in seen_ids:
                    continue
                seen_ids.add(vid)
                videos.append({
                    "id": vid,
                    "title": row.get("title") or "",
                    "duration": row.get("duration"),
                    "upload_date": row.get("upload_date") or "",
                    "url": row.get("url") or f"https://www.youtube.com/watch?v={vid}",
                })
            say(f"  {start}-{end}：{len(rows)} 支（累計 {len(videos)}）")
        start += page
        cache_path.write_text(
            json.dumps({"next_start": start, "videos": videos}, ensure_ascii=False),
            encoding="utf-8")
        # Slower than the per-video pacing below on purpose. A tight loop of
        # page requests a couple of seconds apart is what actually walked
        # into the pagination wall above; a single probe every 8-15s during
        # this tool's own research phase reached far deeper into the same
        # channel's history without ever tripping it. Bursty request rate,
        # not total request count, looks like the trigger.
        time.sleep(10)
    return videos


def in_range(video: dict[str, Any], date_after: str, date_before: str,
             dur_min: int | None, dur_max: int | None) -> bool:
    date = video.get("upload_date") or ""
    if date_after and date and date < date_after:
        return False
    if date_before and date and date > date_before:
        return False
    dur = video.get("duration")
    if dur_min is not None and (dur is None or dur < dur_min):
        return False
    if dur_max is not None and (dur is None or dur > dur_max):
        return False
    return True


def spread_sample(videos: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """`n` videos spread evenly across the list's timespan, not the newest n.

    Sampling the most recent n is what you get for free from a channel page
    and is exactly the wrong thing here -- 30 recent uploads say nothing
    about how the show paced itself across years. Sorted by date first, then
    picked at even index steps across the whole span.
    """
    ordered = sorted(videos, key=lambda v: v.get("upload_date") or "")
    if n >= len(ordered):
        return ordered
    if n <= 0:
        return []
    step = len(ordered) / n
    return [ordered[int(i * step)] for i in range(n)]


def download_captions(video: dict[str, Any], out: Path, langs: list[str],
                      say=print) -> dict[str, Any] | None:
    """This one video's captions, tried one language at a time.

    Returns the manifest row to keep (with real duration/upload_date/title
    filled in from the single-video extraction, which is exact -- the
    flat-playlist listing's date is only approximate) or None if nothing
    downloadable turned up.
    """
    vid = video["id"]
    url = video["url"]
    subs = sorted(out.glob(f"{vid}.*.vtt"))
    got_lang = None
    if not subs:
        # One list-subs call up front. A video with neither manual nor auto
        # captions in *any* language is common (confirmed whole-channel true
        # for one source this tool was built for) -- without this check, an
        # empty video costs one subprocess call per language in `langs`
        # before giving up, for a result that was already knowable after the
        # first one.
        listing = run([url, "--skip-download", "--list-subs", "--no-warnings"],
                      timeout=60)
        said = (listing.stdout or "") + (listing.stderr or "")
        if "has no subtitles" in said and "has no automatic captions" in said:
            return None
        for lang in langs:
            proc = run([url, "--skip-download", "--write-subs", "--write-auto-sub",
                       "--sub-langs", lang, "--convert-subs", "vtt", "--no-warnings",
                       "-o", str(out / f"{vid}.%(ext)s")], timeout=120)
            if is_rate_limited(proc):
                say(f"    429，等 45 秒（{vid} / {lang}）")
                time.sleep(45)
                continue
            subs = sorted(out.glob(f"{vid}.*.vtt"))
            if subs:
                got_lang = lang
                break
            time.sleep(1)
    if not subs:
        return None
    meta = run([url, "--skip-download", "--no-warnings", "--print",
               "%(upload_date)s\t%(duration)s\t%(title)s"], timeout=60)
    line = (meta.stdout or "").strip().splitlines()
    upload_date, duration, title = video.get("upload_date", ""), video.get("duration"), video["title"]
    if line:
        parts = line[0].split("\t")
        if len(parts) == 3:
            upload_date = parts[0] if parts[0] != "NA" else upload_date
            duration = int(parts[1]) if parts[1].isdigit() else duration
            title = parts[2] or title
    if got_lang is None and subs:
        got_lang = subs[0].stem.split(".", 1)[-1] if "." in subs[0].stem else ""
    return {"id": vid, "title": title, "upload_date": upload_date,
            "duration": duration, "lang": got_lang,
            "caption": str(subs[0].relative_to(ROOT)), "url": url}


def load_manifest(path: Path) -> dict[str, Any]:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"videos": []}


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel", required=True,
                   help="@handle、channel URL、UC... id，或直接一個 list=... 播放清單網址")
    ap.add_argument("--query", default="",
                   help="給了就改用這個頻道的『搜尋這個頻道』分頁，而不是列出全部影片"
                        "——用來從一個多主持人輪替的頻道裡只挑一個人的份")
    ap.add_argument("--out", required=True, help="輸出目錄（相對於這個 repo 的 corpus/ 之下建議）")
    ap.add_argument("--label", default="", help="manifest 裡的顯示名稱")
    ap.add_argument("--date-after", default="", help="YYYYMMDD，只收這天（含）以後上傳的")
    ap.add_argument("--date-before", default="", help="YYYYMMDD，只收這天（含）以前上傳的")
    ap.add_argument("--duration-min", type=int, default=None, help="秒")
    ap.add_argument("--duration-max", type=int, default=None, help="秒")
    ap.add_argument("--sample", default="all", help="數字，或 'all' 收全部符合條件的")
    ap.add_argument("--langs", default="zh-Hant,zh-Hant-TW,zh-TW,zh-Hans,zh,en",
                    help="逗號分隔，依序試到有字幕為止")
    ap.add_argument("--sleep", type=float, default=2.0, help="每支影片之間的間隔秒數")
    ap.add_argument("--start-at", type=int, default=1,
                    help="第一次列舉（還沒有 _enum_cache.json 時）從第幾支開始，"
                         "跳過已知不相關的前段可以少踩幾次分頁的 429")
    args = ap.parse_args()

    out = ROOT / args.out if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = load_manifest(manifest_path)
    have_ids = {v["id"] for v in manifest["videos"]}

    if args.query:
        print(f"[1/3] 用頻道內搜尋：{args.channel} 搜「{args.query}」...")
        playlist = search_url(args.channel, args.query)
    else:
        print(f"[1/3] 找 {args.channel} 的 uploads 播放清單...")
        playlist = uploads_playlist_url(args.channel)
    print(f"      -> {playlist}")

    print("[2/3] 列出所有影片（分頁，會花一點時間）...")
    # One cache file per *source*, not per --out. The uploads-playlist walk
    # and a channel-search walk paginate two unrelated result sets under the
    # same channel -- sharing one cache file made a search resume start from
    # an uploads-playlist checkpoint (or the reverse) once, silently mixing
    # two different pagination contexts under one `next_start` pointer.
    import hashlib
    tag = hashlib.sha1(playlist.encode()).hexdigest()[:8]
    videos = enumerate_videos(playlist, out / f"_enum_cache.{tag}.json", start_at=args.start_at)
    print(f"      共 {len(videos)} 支")
    if not videos:
        print("      ⚠ 一支都沒列到 —— 頻道打不開、名稱錯了，或是被擋了，"
              "不是『這頻道真的沒有影片』。")
        save_manifest(manifest_path, manifest)
        return

    manifest["label"] = args.label or manifest.get("label", "")
    manifest["source_channel"] = args.channel

    kept = [v for v in videos
           if in_range(v, args.date_after, args.date_before,
                      args.duration_min, args.duration_max)]
    print(f"      符合日期/長度條件的：{len(kept)}")
    if not kept:
        print("      ⚠ 條件篩完是 0 —— 檢查日期/長度範圍是不是設錯了，"
              "不是這段時間真的沒上傳過影片。")
        manifest["last_run"] = {"checked": len(videos), "matched": 0,
                                "downloaded": 0, "no_captions": 0}
        save_manifest(manifest_path, manifest)
        return

    sample = kept if args.sample == "all" else spread_sample(kept, int(args.sample))
    todo = [v for v in sample if v["id"] not in have_ids]
    print(f"[3/3] 抽樣 {len(sample)} 支，其中 {len(todo)} 支還沒下載過字幕")

    got, empty = 0, 0
    for i, video in enumerate(todo, start=1):
        print(f"  ({i}/{len(todo)}) {video['id']} {video['title'][:40]}")
        row = download_captions(video, out, args.langs.split(","))
        if row:
            manifest["videos"].append(row)
            save_manifest(manifest_path, manifest)   # after every video: a
            got += 1                                 # kill partway through
        else:                                        # loses no earlier work
            empty += 1
        time.sleep(args.sleep)

    if empty:
        print(f"⚠ {empty} 支沒有任何字幕可抓（沒有人上字幕、也沒有自動字幕）")
    # Written even when got == 0 -- a channel that genuinely has no captions
    # on any sampled video is a real, useful answer, and this project has
    # learned the hard way that a warning only spoken to the terminal
    # disappears the moment something restarts. `last_run` keeps this run's
    # counts on disk, next to the videos it found nothing for.
    manifest["last_run"] = {"checked": len(videos), "matched": len(kept),
                            "sampled": len(sample), "downloaded": got,
                            "no_captions": empty}
    save_manifest(manifest_path, manifest)
    print(f"完成：這次新抓到 {got} 支，manifest 累計 {len(manifest['videos'])} 支 -> {manifest_path}")


if __name__ == "__main__":
    main()
