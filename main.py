from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.media import duration, extract_audio, prepare_video
from src.ollama import OllamaClient
from src.render import render
from src.transcribe import (
    GAP_MIN,
    TERMS_MAX,
    correct_with_qwen,
    fill_gaps,
    merge_extra_segments,
    save_transcript,
    transcribe,
)
from src.utils import write_json
from src.visuals import plan_visuals_with_retry, render_cards


ROOT = Path(__file__).resolve().parent
DEFAULT_VIDEO = ROOT / "work" / "source_hlTBcnX3KZE.mp4"


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
    parser.add_argument("--ollama-model", default="qwen2.5:7b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11435")
    parser.add_argument("--ssh-target", default=os.environ.get("CUBA_SSH_TARGET", "yuyu@cuba001"))
    parser.add_argument("--font", default="/System/Library/Fonts/PingFang.ttc")
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


def main() -> None:
    args = parse_args()
    work = ROOT / "work"
    output = (ROOT / args.output).resolve()
    visuals_dir = output / "visuals"
    output.mkdir(parents=True, exist_ok=True)

    print("[1/7] Preparing video")
    video, context = prepare_video(args.source, work)
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
        segments, language = transcribe(audio, args.whisper_model, context, sensitive=args.sensitive)
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
    write_json(output / "run.json", {
        "source": str(video),
        "whisper_model": args.whisper_model,
        "sensitive": args.sensitive,
        "fill_gaps": args.fill_gaps,
        "terms": terms,
    })

    print("[3/7] Connecting to remote Qwen")
    client = OllamaClient(args.ollama_url, args.ollama_model, args.ssh_target)
    client.ensure_ready()

    print("[4/7] Correcting transcript with Qwen")
    segments = correct_with_qwen(client, segments, context)

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
    save_transcript(segments, output / "transcript.txt", output / "subtitles_zh.srt")

    print("[5/7] Planning information cards")
    visual_plan = plan_visuals_with_retry(client, segments, duration(video))
    render_cards(visual_plan, visuals_dir, Path(args.font))
    write_json(output / "ai_visuals.json", visual_plan)

    print("[6/7] Rendering subtitles and cards")
    render(video, output / "subtitles_zh.srt", visual_plan, output / "final.mp4")

    print(f"[7/7] Done: {output / 'final.mp4'}")


if __name__ == "__main__":
    main()
