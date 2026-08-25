from __future__ import annotations

import argparse
import os
from pathlib import Path

from src.media import duration, extract_audio, prepare_video
from src.ollama import OllamaClient
from src.render import render
from src.transcribe import correct_with_qwen, merge_extra_segments, save_transcript, transcribe
from src.utils import write_json
from src.visuals import plan_visuals, render_cards


ROOT = Path(__file__).resolve().parent
DEFAULT_VIDEO = ROOT / "work" / "source_hlTBcnX3KZE.mp4"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a subtitled video with AI-planned information cards.")
    parser.add_argument("source", nargs="?", default=str(DEFAULT_VIDEO), help="YouTube URL or local video file")
    parser.add_argument("--whisper-model", default="medium")
    parser.add_argument("--sensitive", action="store_true", help="Detect quieter or accented speech more aggressively")
    parser.add_argument("--ollama-model", default="qwen2.5:7b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11435")
    parser.add_argument("--ssh-target", default=os.environ.get("CUBA_SSH_TARGET", "yuyu@cuba001"))
    parser.add_argument("--font", default="/System/Library/Fonts/PingFang.ttc")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    work = ROOT / "work"
    output = ROOT / "output"
    visuals_dir = output / "visuals"
    output.mkdir(parents=True, exist_ok=True)

    print("[1/6] Preparing video")
    video, context = prepare_video(args.source, work)
    audio = work / "audio.wav"
    extract_audio(video, audio)

    print("[2/7] Transcribing with faster-whisper")
    segments, language = transcribe(audio, args.whisper_model, context, sensitive=args.sensitive)
    if not segments:
        raise RuntimeError("Whisper returned an empty transcript")
    # Persist costly local recognition before attempting any network service.
    write_json(output / "transcript_raw.json", {"language": language, "segments": segments})

    print("[3/7] Connecting to remote Qwen")
    client = OllamaClient(args.ollama_url, args.ollama_model, args.ssh_target)
    client.ensure_ready()

    print("[4/7] Correcting transcript with Qwen")
    segments = correct_with_qwen(client, segments, context)
    corrections_path = video.with_suffix(".corrections.json")
    if corrections_path.is_file():
        corrections = {
            int(key): value
            for key, value in __import__("json").loads(corrections_path.read_text(encoding="utf-8")).items()
        }
        segments = [{**item, "text": corrections.get(item["id"], item["text"])} for item in segments]
    segments = merge_extra_segments(segments, video.with_suffix(".extra_segments.json"))
    write_json(output / "transcript.json", {"language": language, "segments": segments})
    save_transcript(segments, output / "transcript.txt", output / "subtitles_zh.srt")

    print("[5/7] Planning information cards")
    visual_plan = plan_visuals(client, segments, duration(video))
    render_cards(visual_plan, visuals_dir, Path(args.font))
    write_json(output / "ai_visuals.json", visual_plan)

    print("[6/7] Rendering subtitles and cards")
    render(video, output / "subtitles_zh.srt", visual_plan, output / "final.mp4")

    print(f"[7/7] Done: {output / 'final.mp4'}")


if __name__ == "__main__":
    main()
