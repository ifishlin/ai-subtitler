from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from core import caption, scene
from core.audit import inspect, report, write as write_audit
from core.repair import repair
from core.review import review_output
from core.media import duration, extract_audio, prepare_video
from core import llm, settings
from core.compose import compose
from core.proofread import correct_with_qwen, review_hallucinations
from core.subtitles import merge_extra_segments, save_transcript
from core.transcribe import GAP_MIN, TERMS_MAX, fill_gaps, transcribe
from core.translate import translate_with_qwen
from core.utils import write_json
from core.visuals import plan_visuals_with_retry, render_cards


ROOT = Path(__file__).resolve().parent
DEFAULT_VIDEO = ROOT / "work" / "source_hlTBcnX3KZE.mp4"

# Cards carry Chinese, so the font has to as well. The pipeline runs on both
# macOS and Linux, and neither has the other's fonts.
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
]


def find_font(chosen: str | None) -> Path:
    if chosen:
        return Path(chosen)
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return Path(candidate)
    raise SystemExit(
        "找不到中文字型，請用 --font 指定一個 .ttc/.ttf 檔案。\n"
        "已尋找：" + "、".join(FONT_CANDIDATES)
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a subtitled video with AI-planned information cards.")
    parser.add_argument("source", nargs="?", default=str(DEFAULT_VIDEO), help="YouTube URL or local video file")
    parser.add_argument("--whisper-model", default="medium")
    parser.add_argument("--sensitive", action="store_true", help="Detect quieter or accented speech more aggressively")
    parser.add_argument(
        "--fill-gaps",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Transcribe normally, then revisit only the silent stretches in sensitive mode. "
             "Recovers street and telephone interviews without the whole-video error rate of --sensitive. "
             "Skips the id-keyed corrections sidecar, whose ids no longer line up once gaps are filled; "
             "its proper nouns are passed to Whisper as hotwords instead.",
    )
    parser.add_argument("--gap-min", type=float, default=GAP_MIN,
                        help="Shortest silence worth revisiting, in seconds")
    parser.add_argument("--output", default="output", help="Directory for the results")
    parser.add_argument(
        "--reuse-transcript", metavar="FILE",
        help="Skip both Whisper passes and start from an existing transcript_raw.json. "
             "Recognition is the slowest stage, so this re-runs correction, cards and "
             "rendering without repeating it. Implies --fill-gaps handling of sidecars.",
    )
    parser.add_argument("--llm", choices=llm.choices(),
                        help=f"誰來校對、翻譯、審查、規劃圖卡。{llm.help_text()}")
    parser.add_argument("--llm-model",
                        help="Override the provider's model, e.g. a cheaper one for a batch")
    parser.add_argument("--llm-effort", choices=["low", "medium", "high"],
                        help="How hard the model should think, where the provider offers it")
    parser.add_argument("--llm-log", metavar="FILE",
                        help="Record every exchange, so the run can be replayed with --llm replay")
    parser.add_argument("--replay-from", metavar="FILE",
                        help="Answers for --llm replay; defaults to --llm-log's file")
    parser.add_argument("--ollama-model")
    parser.add_argument("--ollama-url")
    parser.add_argument("--ssh-target", default=os.environ.get("CUBA_SSH_TARGET", "yuyu@cuba001"))
    parser.add_argument("--font", default=None,
                        help="CJK font for the information cards; found automatically if omitted")
    parser.add_argument(
        "--subtitles", choices=["auto", "source", "zh", "bilingual"], default="auto",
        help="Which subtitles to burn in. auto keeps a Chinese video as spoken and "
             "gives any other language both languages on screen. source means the "
             "spoken language only, and skips translation altogether.",
    )
    parser.add_argument(
        "--layout", choices=["full", "inset"], default="full",
        help="full burns captions over the whole picture; inset places the video "
             "in the upper left of a pale field, leaving room for explanation.",
    )
    parser.add_argument(
        "--badges", metavar="DIR",
        help="Directory of square images to show in the corner of an inset "
             "layout, one at a time, cycling through them.",
    )
    parser.add_argument("--badge-every", type=float, default=60.0,
                        help="Seconds each corner image stays before the next")
    parser.add_argument(
        "--render", action="store_true",
        help="Burn the video now. By default the run stops after subtitles and "
             "cards, since the encode takes minutes and the result is superseded "
             "by whatever the review changes. Render from the editor instead.",
    )
    parser.add_argument(
        "--no-recut", action="store_true",
        help="Keep Whisper's own caption boundaries. By default captions are "
             "rebuilt from word timings, which stops a word being split across "
             "two lines and caps how long and dense a line may be.",
    )
    parser.add_argument(
        "--no-correct", action="store_true",
        help="Skip the Qwen proofreading pass. Worth using on English, where "
             "recognition is already accurate and rewriting mostly adds risk.",
    )
    return parser.parse_args()


def glossary(video: Path) -> str:
    """Proper nouns for the gap pass, from an optional VIDEO.terms.txt sidecar.

    One term per line, blank lines and #-comments ignored. Terms fix a name
    wherever it is heard, unlike the id-keyed corrections sidecar which pins each
    correction to a single segment number. They are not derived from that sidecar
    because its values are whole clauses, and feeding clauses to the recogniser as
    hotwords biases it towards repeating them.
    """
    path = video.with_suffix(".terms.txt")
    if not path.is_file():
        return ""
    terms = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return "、".join(terms)[:TERMS_MAX]


def resumed_source(reuse: str) -> str | None:
    """The video the reused run came from.

    Without this a resumed run falls back to the hard-coded default video and
    silently burns one run's subtitles onto another run's picture.
    """
    run = Path(reuse).resolve().parent / "run.json"
    if not run.is_file():
        return None
    source = json.loads(run.read_text(encoding="utf-8")).get("source")
    return source if source and Path(source).is_file() else None


def main() -> None:
    args = parse_args()
    work = ROOT / "work"
    output = (ROOT / args.output).resolve()
    visuals_dir = output / "visuals"
    output.mkdir(parents=True, exist_ok=True)

    source = args.source
    if args.reuse_transcript:
        inherited = resumed_source(args.reuse_transcript)
        if inherited:
            source = inherited
            print(f"      沿用 {Path(args.reuse_transcript).parent.name} 的影片：{Path(source).name}")
        elif source == str(DEFAULT_VIDEO):
            raise SystemExit(
                f"{args.reuse_transcript} 旁邊沒有 run.json，無法判斷影片來源。"
                "請把影片路徑當第一個參數傳入，否則會用錯影片。"
            )

    print("[1/8] Preparing video")
    video, context = prepare_video(source, work)
    audio = work / "audio.wav"
    extract_audio(video, audio)

    terms = glossary(video) if args.fill_gaps else ""
    if terms:
        print(f"      空隙辨識的專有名詞提示：{terms}")

    if args.reuse_transcript:
        stored = json.loads(Path(args.reuse_transcript).read_text(encoding="utf-8"))
        segments, language = stored["segments"], stored.get("language", "zh")
        print(f"[2/8] Reusing {args.reuse_transcript} ({len(segments)} segments)")
    else:
        print("[2/8] Transcribing with faster-whisper")
        segments, language = transcribe(
            audio, args.whisper_model, sensitive=args.sensitive, recut=not args.no_recut
        )
    if not segments:
        raise RuntimeError("Whisper returned an empty transcript")

    if args.fill_gaps and not args.reuse_transcript:
        total = duration(video)
        segments, gap_report = fill_gaps(
            video, work, args.whisper_model, segments, total, terms=terms, min_gap=args.gap_min
        )
        write_json(output / "gap_report.json", gap_report)
        for entry in gap_report:
            start, end = entry["window"]
            print(f"      空隙 {start:.1f}-{end:.1f}s")
            for item in entry["kept"]:
                print(f"        + {item['start']:.1f}s  {item['text']}")
            for text, reason in entry["dropped"]:
                print(f"        - {text}  （{reason}）")
        recovered = sum(len(entry["kept"]) for entry in gap_report)
        print(f"      共補回 {recovered} 句，捨棄 {sum(len(e['dropped']) for e in gap_report)} 句")

    # Persist costly local recognition before attempting any network service.
    write_json(output / "transcript_raw.json", {"language": language, "segments": segments})
    # The review UI reads this to find the source video for this run.
    # A resumed run inherits the recognition of the run it reuses, so report
    # how those segments were produced rather than which flags this call had.
    recovered = sum(1 for item in segments if item.get("origin") == "gap")
    write_json(output / "run.json", {
        "source": str(video),
        "whisper_model": args.whisper_model,
        "sensitive": args.sensitive,
        "fill_gaps": args.fill_gaps or recovered > 0,
        "recovered_segments": recovered,
        "reused": args.reuse_transcript or None,
        "terms": terms,
    })

    # The model only proofreads, translates, reviews and plans cards. Recognition
    # is already done and rendering needs nothing from it, so an unreachable
    # model should cost those four things -- not the whole run and the minutes
    # already spent on it.
    provider = settings.llm_options(args)[0]
    print(f"[3/8] Connecting to {provider}")
    provider, options = settings.llm_options(args)
    client = llm.build(provider, options)
    try:
        if client is not None:
            client.ensure_ready()
    except Exception as error:                                    # noqa: BLE001
        print(f"      {provider} 無法連線：{error}")
        print("      跳過校正、翻譯與圖卡；字幕與影片仍會產出")
        client = None

    if client is None or args.no_correct:
        print(f"[4/8] Skipping proofreading（辨識語言：{language}）")
    else:
        print(f"[4/8] Correcting transcript（辨識語言：{language}）")
        # Reading the transcript catches invented lines the character filter
        # cannot: fluent, correctly encoded, and about something else.
        segments = review_hallucinations(client, segments)
        segments = correct_with_qwen(client, segments, context, language=language)

    # Translating is pointless when only the spoken language will be shown.
    if language.startswith("zh") or client is None:
        pass
    elif args.subtitles == "source":
        print("      --subtitles source：不翻譯，只保留原文")
    elif all(item.get("zh") for item in segments):
        # A resumed run may already carry a translation -- reviewed by hand, or
        # produced by a better model than the one configured here. Redoing it
        # would silently discard that work.
        print(f"      沿用既有的 {len(segments)} 段譯文，不重新翻譯")
    else:
        print("      翻譯成繁體中文")
        segments = translate_with_qwen(client, segments, context)

    if not (args.fill_gaps or args.reuse_transcript):
        # The id-keyed sidecars only line up with the plain single-pass run.
        corrections_path = video.with_suffix(".corrections.json")
        if corrections_path.is_file():
            corrections = {
                int(key): value
                for key, value in json.loads(corrections_path.read_text(encoding="utf-8")).items()
            }
            missing = sorted(set(corrections) - {item["id"] for item in segments})
            if missing:
                print(f"      warning: corrections for missing segment ids ignored: {missing}")
            segments = [{**item, "text": corrections.get(item["id"], item["text"])} for item in segments]
        segments = merge_extra_segments(segments, video.with_suffix(".extra_segments.json"))

    # Check, mend, check again. The audit used to run last, where nothing could
    # act on what it found; here its arithmetic findings are repaired before
    # anyone is asked to read anything, and the second pass is what decides
    # whether the repairs were an improvement at all.
    print("[5/8] 檢查與自動修補")
    total = duration(video)
    before = inspect(segments, video, total)
    print(f"      修補前 {before['score']} 分，{len(before['findings'])} 項")
    mended, notes = repair(segments, duration=total)
    if notes:
        after = inspect(mended, video, total)
        if after["score"] <= before["score"]:
            for line in notes:
                print(f"      {line}")
            print(f"      修補後 {after['score']} 分，{len(after['findings'])} 項")
            segments, verdict = mended, after
        else:
            # Mending made it worse, so none of it is kept. What the reviewer
            # sees should be the original fault, not this pass's guess at it.
            print(f"      修補後 {after['score']} 分，比修補前差，全部退回")
            verdict = {**before, "needs_eyes": True}
    else:
        print("      沒有可以機械修補的問題")
        verdict = before

    # Review is last and reads the finished thing, because proofreading and
    # translation are themselves edits that can go wrong -- a review that runs
    # before them cannot see what they did.
    if client is not None and not args.no_correct:
        print("[6/8] LLM 審查成品")
        segments, judged = review_output(client, segments, language=language)
        if judged:
            print(f"      {judged}")
        verdict = inspect(segments, video, total)

    write_audit(output / "audit.json", verdict)
    print(report(verdict))
    write_json(output / "transcript.json", {"language": language, "segments": segments})
    written = save_transcript(segments, output / "transcript.txt", output / "subtitles_zh.srt")

    choice = args.subtitles
    if choice == "auto":
        choice = "bilingual" if "bilingual" in written else "zh"
    burn_srt = written.get(choice) or written["zh"]
    print(f"      字幕檔：{', '.join(sorted(p.name for p in written.values()))}（燒錄用 {burn_srt.name}）")

    print("[7/8] Planning information cards")
    visual_plan = (
        plan_visuals_with_retry(client, segments, duration(video)) if client else []
    )
    if client is None:
        print(f"      沒有 {provider} 可用，這次不加圖卡")
    render_cards(visual_plan, visuals_dir, find_font(args.font))
    write_json(output / "ai_visuals.json", visual_plan)

    if args.render:
        print(f"[8/8] Rendering subtitles and cards（版面：{args.layout}）")
        # One frame, one renderer. The layout is a scene either way, so the
        # editor can open this run and see exactly what was burned.
        stage = (scene.default_scene(burn_srt.name) if args.layout == "inset"
                 else scene.full_scene(burn_srt.name))
        if args.badges:
            images = sorted(Path(args.badges).glob("*.png"))
            scene.add_badges(stage, images, duration(video), args.badge_every)
            if images:
                print(f"      角落圖示 {len(images)} 張，每 {args.badge_every:.0f} 秒輪替")
        scene.add_cards(stage, visual_plan)
        scene.save(output / "scene.json", stage)

        # Captions are drawn once and overlaid as pictures, which is what lets
        # the editor show the real thing rather than an approximation of it.
        element = scene.one(stage, "subtitle")
        listing = None
        if element:
            built = caption.build(caption.read_srt(burn_srt), element,
                                  tuple(stage["canvas"]), output / "captions")
            stage["caption_band"] = built["band"]
            listing = caption.playlist(built["captions"], duration(video),
                                       built["band"], output / "captions")
        compose(video, stage, output / "final.mp4", srt_dir=output,
                image_root=Path.cwd(), captions=listing)
    else:
        print("[8/8] 跳過燒錄（加 --render 可直接出片）")
        print(f"      字幕與圖卡已就緒，在編輯器確認後再燒：")
        print(f"      .venv/bin/python studio/server.py --output {args.output}")

    done = output / "final.mp4" if args.render else output / "subtitles_zh.srt"
    spent = getattr(client, "usage", None)
    if spent is not None:
        print(f"      {spent.line()}")
    print(f"Done: {done}")


if __name__ == "__main__":
    main()
