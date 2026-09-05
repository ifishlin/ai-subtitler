"""Transcribe a corpus channel's videos with Whisper, on a remote host.

For a channel `tools/fetch_channel_captions.py` already confirmed has no
captions -- manual or auto-generated, in any language -- on any of its
sampled videos. That tool leaves `manifest.json`'s `"videos"` list empty in
that case (it only appends a row when a caption downloads), so the actual
video list to work from lives in the directory's `_enum_cache*.json` files
instead; this tool reads those.

Runs the real `core/transcribe.py` `transcribe()` -- same VAD filtering,
hallucination dropping, `recut_segments()` boundary tidying, s2twp OpenCC
conversion -- not a reimplementation, by syncing that module (and the small
slice of `core/` it imports) to the remote host and running it there. Whisper
"medium" on a modern many-core CPU is far faster than on a 2018 laptop with no
GPU, which is the entire reason this runs remotely rather than locally.

Only transcript text is kept: downloaded audio is deleted immediately after
each video's transcription, on the remote host, and never copied back here.

Usage:

    python tools/whisper_transcribe_channel.py \\
        --channel-dir "corpus/曾秋教室/三年三班" \\
        --host yuyu@cuba001 --model medium

    # smoke-test on 2 videos before committing to the whole channel
    python tools/whisper_transcribe_channel.py \\
        --channel-dir "corpus/曾秋教室/三年三班" --limit 2

Resumable: a video whose `<id>.transcript.json` already exists in
`--channel-dir` is skipped, both locally (never re-queued) and on the remote
`--out` directory (worker skips it too) -- an interrupted run only costs the
video in flight, not the whole batch.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
WORKER_LOCAL = ROOT / "tools" / "_whisper_worker.py"
CORE_FILES = ["__init__.py", "transcribe.py", "segment.py", "media.py", "ollama.py", "utils.py", "audit.py"]


def collect_videos(channel_dir: Path) -> list[dict[str, Any]]:
    """Every video `fetch_channel_captions.py` found for this channel.

    A channel with zero captions anywhere never gets a `manifest["videos"]`
    row (see that tool's `download_captions()`), so the full list is only in
    its enumeration cache(s) -- one file per distinct playlist/search URL it
    ever paginated, hence the glob rather than a single fixed name.
    """
    seen: dict[str, dict[str, Any]] = {}
    for cache in sorted(channel_dir.glob("_enum_cache*.json")):
        data = json.loads(cache.read_text(encoding="utf-8"))
        for v in data.get("videos", []):
            seen[v["id"]] = v
    return list(seen.values())


def sync_worker(host: str, remote_dir: str) -> None:
    print(f"[1/4] 同步 core/ 和 worker 到 {host}:{remote_dir} ...")
    subprocess.run(["ssh", host, f"mkdir -p {remote_dir}/core"], check=True)
    subprocess.run(
        ["rsync", "-az", *[str(ROOT / "core" / f) for f in CORE_FILES], f"{host}:{remote_dir}/core/"],
        check=True,
    )
    subprocess.run(["rsync", "-az", str(WORKER_LOCAL), f"{host}:{remote_dir}/worker.py"], check=True)


def run_remote_batch(host: str, remote_dir: str, model: str, jobs: list[dict[str, Any]],
                      sleep: float, local_tmp: Path, omp_threads: int, poll_seconds: float) -> Path:
    """Launches the worker detached on the remote host, polls until every job
    is accounted for, then returns the local dir `out/` was rsynced into.

    A whole-channel batch runs for hours (measured: faster-whisper's default
    `cpu_threads=0` only drove ~3.4 of cuba001's cores -- 337% CPU on a 25s
    clip -- so a 50-video run at that rate would take roughly as long as the
    total *content* runs, not 8-16x faster as this host's earlier benchmark
    suggested; `OMP_NUM_THREADS` roughly doubled that to ~10 cores with no
    code change). A single foreground `ssh` blocking for that long dies with
    the connection; `nohup ... &` detaches it so the batch survives a dropped
    SSH session, and this polls the worker's own status file rather than
    holding the connection open.
    """
    jobs_path = local_tmp / "jobs.json"
    jobs_path.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    subprocess.run(["scp", str(jobs_path), f"{host}:{remote_dir}/jobs.json"], check=True)

    print(f"[2/4] 在 {host} 上跑 {len(jobs)} 支影片（model={model}, OMP_NUM_THREADS={omp_threads}）...")
    remote_cmd = (
        f"cd {remote_dir} && rm -f worker.log && "
        f"OMP_NUM_THREADS={omp_threads} PATH=$HOME/bin:$PATH nohup "
        f".venv/bin/python worker.py --jobs jobs.json --model {model} --out out --sleep {sleep} "
        f"> worker.log 2>&1 < /dev/null & echo $!"
    )
    result = subprocess.run(["ssh", host, remote_cmd], check=True, text=True, capture_output=True)
    pid = result.stdout.strip().splitlines()[-1]
    print(f"      已在背景啟動，remote pid={pid}，之後每 {poll_seconds:.0f} 秒查一次進度")

    expected_ids = {job["id"] for job in jobs}
    wanted = len(expected_ids)
    while True:
        time.sleep(poll_seconds)
        status = subprocess.run(
            ["ssh", host, f"cat {remote_dir}/out/_status.jsonl 2>/dev/null"],
            check=True, text=True, capture_output=True,
        ).stdout
        seen: dict[str, dict[str, Any]] = {}
        for line in status.splitlines():
            if line.strip():
                row = json.loads(line)
                seen[row["id"]] = row
        done_ids = expected_ids & set(seen)
        ok = sum(1 for i in done_ids if seen[i].get("ok"))
        bad = len(done_ids) - ok
        print(f"      進度：{len(done_ids)}/{wanted}（成功 {ok}，失敗 {bad}）")
        if len(done_ids) >= wanted:
            break
        alive = subprocess.run(["ssh", host, f"kill -0 {pid} 2>/dev/null && echo alive || echo dead"],
                               check=True, text=True, capture_output=True).stdout.strip()
        if alive == "dead":
            tail = subprocess.run(["ssh", host, f"tail -60 {remote_dir}/worker.log"],
                                  check=True, text=True, capture_output=True).stdout
            print(f"      ⚠ 遠端 worker 提前結束（{len(done_ids)}/{wanted}），worker.log 最後 60 行：\n{tail}")
            break

    print("[3/4] 把逐字稿抓回來 ...")
    out_local = local_tmp / "out"
    out_local.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsync", "-az", f"{host}:{remote_dir}/out/", str(out_local) + "/"], check=True)
    return out_local


def update_manifest(channel_dir: Path, out_local: Path, jobs_by_id: dict[str, dict[str, Any]],
                     model: str) -> tuple[int, int]:
    manifest_path = channel_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    have_ids = {v["id"] for v in manifest["videos"]}

    done = failed = 0
    status_path = out_local / "_status.jsonl"
    statuses = {}
    if status_path.is_file():
        for line in status_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                statuses[row["id"]] = row

    for transcript_path in sorted(out_local.glob("*.transcript.json")):
        vid = transcript_path.stem.replace(".transcript", "")
        payload = json.loads(transcript_path.read_text(encoding="utf-8"))
        dest = channel_dir / f"{vid}.transcript.json"
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if vid not in have_ids:
            job = jobs_by_id.get(vid, {})
            manifest["videos"].append({
                "id": vid,
                "title": job.get("title", ""),
                "upload_date": job.get("upload_date", ""),
                "duration": job.get("duration"),
                "url": job.get("url", f"https://www.youtube.com/watch?v={vid}"),
                "source": "whisper",              # explicit: not youtube-captions
                "model": model,
                "language": payload.get("language", ""),
                "transcript": str(dest.relative_to(ROOT)),
            })
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        done += 1

    for vid, row in statuses.items():
        if not row.get("ok") and not (channel_dir / f"{vid}.transcript.json").is_file():
            failed += 1
            print(f"  ⚠ {vid} 失敗：{row.get('error', '')[:200]}")

    return done, failed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channel-dir", required=True, help="相對於這個 repo 的 corpus 子目錄")
    ap.add_argument("--host", default="yuyu@cuba001")
    ap.add_argument("--remote-dir", default="~/whisper-job")
    ap.add_argument("--model", default="medium")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 支，先小量驗證再跑全部")
    ap.add_argument("--sleep", type=float, default=3.0, help="每支影片下載之間的間隔秒數")
    ap.add_argument("--omp-threads", type=int, default=16,
                    help="OMP_NUM_THREADS：faster-whisper 預設 cpu_threads=0 實測只吃 ~3.4 核，"
                         "調高這個環境變數不必動 core/transcribe.py 就能吃到更多核心")
    ap.add_argument("--poll-seconds", type=float, default=60.0, help="輪詢遠端進度的間隔秒數")
    args = ap.parse_args()

    channel_dir = ROOT / args.channel_dir if not Path(args.channel_dir).is_absolute() else Path(args.channel_dir)
    manifest_path = channel_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"找不到 {manifest_path}")

    videos = collect_videos(channel_dir)
    todo = [v for v in videos if not (channel_dir / f"{v['id']}.transcript.json").is_file()]
    if args.limit:
        todo = todo[:args.limit]
    print(f"共 {len(videos)} 支影片，{len(todo)} 支還沒有 whisper 逐字稿")
    if not todo:
        print("沒有要做的，結束")
        return

    sync_worker(args.host, args.remote_dir)

    local_tmp = channel_dir / "_whisper_tmp"
    local_tmp.mkdir(exist_ok=True)
    t0 = time.time()
    out_local = run_remote_batch(args.host, args.remote_dir, args.model, todo, args.sleep, local_tmp,
                                 args.omp_threads, args.poll_seconds)
    elapsed = time.time() - t0

    jobs_by_id = {v["id"]: v for v in todo}
    done, failed = update_manifest(channel_dir, out_local, jobs_by_id, args.model)

    print(f"[4/4] 完成：這次 {len(todo)} 支裡，{done} 支存到逐字稿，{failed} 支失敗")
    print(f"      總耗時 {elapsed/60:.1f} 分鐘，平均每支 {elapsed/max(len(todo),1):.0f} 秒（含下載）")

    # Only staging copies of files already written to channel_dir -- safe to
    # clear on every run rather than accumulating one directory per invocation.
    shutil.rmtree(local_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
