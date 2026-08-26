from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.audit import inspect, report, write as write_audit
from src.media import duration, extract_audio, prepare_video
from src.claude import build as build_client
from src.render import render
from src.transcribe import (
    GAP_MIN,
    review_hallucinations,
    TERMS_MAX,
    correct_with_qwen,
    fill_gaps,
    merge_extra_segments,
    save_transcript,
    transcribe,
    translate_with_qwen,
)
from src.utils import write_json
from src.visuals import plan_visuals_with_retry, render_cards


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
        action="store_true",
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
    parser.add_argument("--llm", choices=["qwen", "claude"], default="qwen",
                        help="Which language model proofreads, translates and plans cards")
    parser.add_argument("--ollama-model", default="qwen2.5:7b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11435")
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

    print("[1/7] Preparing video")
    video, context = prepare_video(source, work)
    audio = work / "audio.wav"
    extract_audio(video, audio)

    terms = glossary(video) if args.fill_gaps else ""
    if terms:
        print(f"      空隙辨識的專有名詞提示：{terms}")

    if args.reuse_transcript:
        stored = json.loads(Path(args.reuse_transcript).read_text(encoding="utf-8"))
        segments, language = stored["segments"], stored.get("language", "zh")
        print(f"[2/7] Reusing {args.reuse_transcript} ({len(segments)} segments)")
    else:
        print("[2/7] Transcribing with faster-whisper")
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

    # Qwen only proofreads, translates and plans cards. Recognition is already
    # done and rendering needs nothing from it, so an unreachable model should
    # cost those three things -- not the whole run and the minutes already spent.
    print(f"[3/7] Connecting to {args.llm}")
    client = build_client(args.llm, args.ollama_url, args.ollama_model, args.ssh_target)
    try:
        client.ensure_ready()
    except Exception as error:                                    # noqa: BLE001
        print(f"      {args.llm} 無法連線：{error}")
        print("      跳過校正、翻譯與圖卡；字幕與影片仍會產出")
        client = None

    if client is None or args.no_correct:
        print(f"[4/7] Skipping proofreading（辨識語言：{language}）")
    else:
        print(f"[4/7] Correcting transcript（辨識語言：{language}）")
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
    write_json(output / "transcript.json", {"language": language, "segments": segments})
    written = save_transcript(segments, output / "transcript.txt", output / "subtitles_zh.srt")

    choice = args.subtitles
    if choice == "auto":
        choice = "bilingual" if "bilingual" in written else "zh"
    burn_srt = written.get(choice) or written["zh"]
    print(f"      字幕檔：{', '.join(sorted(p.name for p in written.values()))}（燒錄用 {burn_srt.name}）")

    print("[5/7] Planning information cards")
    visual_plan = (
        plan_visuals_with_retry(client, segments, duration(video)) if client else []
    )
    if client is None:
        print("      沒有 Qwen 連線，這次不加圖卡")
    render_cards(visual_plan, visuals_dir, find_font(args.font))
    write_json(output / "ai_visuals.json", visual_plan)

    print("[6/7] Rendering subtitles and cards")
    render(video, burn_srt, visual_plan, output / "final.mp4")

    # The run judges its own output, so a batch only needs eyes on what failed.
    audit = inspect(segments, video, duration(video))
    write_audit(output / "audit.json", audit)
    print()
    print(report(audit))
    print()
    print(f"[7/7] Done: {output / 'final.mp4'}")


if __name__ == "__main__":
    main()
