"""Remote-side worker for tools/whisper_transcribe_channel.py.

Runs on the transcription host (cuba001), not on this laptop -- it is synced
there by the driver script over rsync, alongside a small subset of `core/`
(`transcribe.py` and everything it imports). One process handles the whole
batch on purpose: `core/transcribe.py`'s `_model()` caches the loaded
WhisperModel in a module-level dict, and a fresh process per video would pay
the "medium" model's load cost 50 times instead of once.

Not meant to be run by hand -- see tools/whisper_transcribe_channel.py's
docstring for the actual usage.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.media import extract_audio      # noqa: E402
from core.transcribe import transcribe    # noqa: E402


def yt_dlp_audio(url: str, vid: str, work_dir: Path, ytdlp_bin: str) -> Path:
    """Best audio-only stream, no video -- this project has hit 429s before
    on bursty yt-dlp calls (docs/MISTAKES.md), so a 429 here backs off and
    retries rather than treating it as a hard failure.
    """
    out_tmpl = str(work_dir / f"{vid}_src.%(ext)s")
    for existing in work_dir.glob(f"{vid}_src.*"):
        existing.unlink()
    for attempt in range(1, 4):
        proc = subprocess.run(
            [ytdlp_bin, "--no-check-certificates", "--no-playlist",
             "-f", "bestaudio/best", "--restrict-filenames",
             "-o", out_tmpl, "--print", "after_move:filepath", url],
            capture_output=True, text=True, timeout=300,
        )
        said = (proc.stdout or "") + (proc.stderr or "")
        if "429" in said or "Too Many Requests" in said:
            wait = 30 * attempt
            print(f"    429，等 {wait} 秒重試（第 {attempt} 次）", flush=True)
            time.sleep(wait)
            continue
        if proc.returncode != 0:
            raise RuntimeError(f"yt-dlp 失敗：{proc.stderr[-500:]}")
        lines = proc.stdout.strip().splitlines()
        if not lines:
            raise RuntimeError("yt-dlp 沒有回傳檔案路徑")
        return Path(lines[-1]).resolve()
    raise RuntimeError("yt-dlp 429，重試 3 次都沒解除")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True, help="JSON list of {id,url,title,...}")
    ap.add_argument("--model", default="medium")
    ap.add_argument("--out", required=True, help="output dir for <id>.transcript.json")
    ap.add_argument("--sleep", type=float, default=3.0, help="pause between videos")
    ap.add_argument("--ytdlp", default=str(Path.home() / "whisper-job" / ".venv" / "bin" / "yt-dlp"))
    args = ap.parse_args()

    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path("audio_tmp")
    work_dir.mkdir(exist_ok=True)
    status_path = out_dir / "_status.jsonl"

    for i, job in enumerate(jobs, start=1):
        vid = job["id"]
        result_path = out_dir / f"{vid}.transcript.json"
        if result_path.is_file():
            print(f"({i}/{len(jobs)}) {vid} 已完成，跳過", flush=True)
            continue
        print(f"({i}/{len(jobs)}) {vid} {job.get('title', '')[:40]}", flush=True)
        t0 = time.time()
        raw_audio = wav_path = None
        try:
            raw_audio = yt_dlp_audio(job["url"], vid, work_dir, args.ytdlp)
            download_s = time.time() - t0
            wav_path = work_dir / f"{vid}.wav"
            extract_audio(raw_audio, wav_path)
            t1 = time.time()
            segments, language = transcribe(wav_path, args.model, sensitive=False, recut=True)
            transcribe_s = time.time() - t1
            payload = {
                "id": vid,
                "source": "whisper",
                "model": args.model,
                "language": language,
                "segments": segments,
                "download_seconds": round(download_s, 1),
                "transcribe_seconds": round(transcribe_s, 1),
            }
            # Write under a temp name and rename into place: a process killed
            # mid-write (timeout, Ctrl-C, OOM from a concurrent job) must never
            # leave a half-written *.transcript.json that a later resume scan
            # would mistake for a finished one.
            tmp = out_dir / f"{vid}.transcript.json.tmp"
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.rename(result_path)
            with status_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "id": vid, "ok": True, "download_s": round(download_s, 1),
                    "transcribe_s": round(transcribe_s, 1), "segments": len(segments),
                }, ensure_ascii=False) + "\n")
            print(f"    完成：下載 {download_s:.0f}s，轉錄 {transcribe_s:.0f}s，{len(segments)} 段字幕", flush=True)
        except Exception as exc:  # noqa: BLE001 -- one bad video must not kill the batch
            with status_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"id": vid, "ok": False, "error": str(exc)[:300]}, ensure_ascii=False) + "\n")
            print(f"    失敗：{exc}", flush=True)
        finally:
            for p in (raw_audio, wav_path):
                if p and p.exists():
                    p.unlink()
        time.sleep(args.sleep)


if __name__ == "__main__":
    main()
