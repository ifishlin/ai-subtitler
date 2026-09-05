#!/usr/bin/env python3
"""比較 cuba001 上幾支「看得懂圖」的 Ollama 模型，測試對象是這個專案自己的
背景照片庫（`assets/backgrounds.json` + `assets/backgrounds/*.jpg`）。

## 這支腳本在測什麼

每張背景照片存下來的時候，都記了當初用哪個英文關鍵字（`term`）去 Pexels
搜到它——那個關鍵字就是一個粗略但現成的「這張照片該長什麼樣子」的標籤。
送一張圖給模型，請它用一句英文描述看到什麼，再拿描述跟關鍵字做字詞重疊
比對，就有一個不精確、但不用人工標註就能跑的「說得準不準」訊號。

真正的類別是「照片庫的 21 大類」（`core.backgrounds.categories_of()`），
每一類底下平均 12 個關鍵字、每個關鍵字平均只存 5 張圖——關鍵字本身的
樣本太小，不夠當「每組抽 10-15 張」的組。所以這裡的抽樣單位是**類別**，
不是關鍵字：`--per-group` 張／類別 × 22 類（21 大類 + 未分類），
預設 6 張／類別 ≈ 132 張，落在「約 100-150 張」的目標範圍內。

## 為什麼是「模型在外層迴圈、圖片在內層」

cuba001 沒有 GPU，Ollama 一次只把一個模型留在記憶體——如果圖片在外層、
模型在內層，等於每一張圖都要重新把下一個模型整個讀進記憶體一次，讀取的
時間會被圖片張數放大好幾倍。所以這裡固定「一個模型跑完所有抽樣圖片，
才換下一個模型」，讀取模型的成本只付一次。

## 重新執行

`--models`／`--per-group`／`--seed`／`--timeout` 都是參數，任何一次都可以
只挑其中幾個模型、或換一批抽樣重新跑一次，不用改程式碼。
"""
from __future__ import annotations

import argparse
import base64
import json
import random
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_MODELS = ["qwen2.5vl:7b", "moondream:latest", "llava:7b", "llama3.2-vision:11b"]
DEFAULT_URL = "http://127.0.0.1:11435"
DEFAULT_SSH_TARGET = "yuyu@cuba001"

# 四支模型都送同一句話，才是公平比較。要求「只講看得到的」，不是猜這張圖
# 想表達什麼——跟關鍵字比對的是畫面內容，不是弦外之音。
PROMPT = (
    "Describe in one short sentence, in plain English, what is visibly in "
    "this photo: the setting, main objects, people or animals, and visible "
    "action. Only describe what you can literally see. Do not guess a "
    "location name, an event, or a deeper meaning behind it."
)

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "with", "and", "or", "to",
    "for", "is", "are", "this", "that", "photo", "image", "picture",
    "showing", "shows", "shown", "view", "close", "up", "some", "several",
    "person", "people", "background", "foreground", "one", "two", "few",
}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower())
            if w not in STOPWORDS and len(w) > 2}


def match_score(term: str, description: str) -> tuple[bool, float]:
    """粗略的字詞重疊比對：關鍵字（`term`）裡有意義的字，模型描述裡有沒有提到。

    這是近似訊號，不是精確準確率——同義詞（"cash" vs "banknotes"）算不進去，
    這件事在結果檔案裡會註明。
    """
    term_tokens = _tokens(term)
    if not term_tokens:
        return False, 0.0
    desc_tokens = _tokens(description)
    overlap = term_tokens & desc_tokens
    return (len(overlap) > 0), round(len(overlap) / len(term_tokens), 3)


def ensure_tunnel(base_url: str, ssh_target: str) -> None:
    """跟 `core/ollama.py` 的 `OllamaClient.ensure_ready()` 同一套邏輯：
    先直接探，探不到才開 SSH tunnel。這裡不重用那支 class，因為它會順便
    檢查「這個模型有沒有裝」，而這支腳本一次要測好幾個模型。
    """
    try:
        urllib.request.urlopen(f"{base_url}/api/tags", timeout=4).close()
        return
    except (OSError, urllib.error.URLError):
        pass
    if not ssh_target:
        raise RuntimeError("連不到 Ollama，而且沒有指定 --ssh-target 可以開 tunnel")
    port = base_url.rsplit(":", 1)[-1]
    forward = [
        "ssh", "-f", "-N", "-L", f"{port}:127.0.0.1:11434",
        "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", ssh_target,
    ]
    attempt = subprocess.run(forward, capture_output=True, text=True)
    if attempt.returncode:
        subprocess.run(["pkill", "-f", f"ssh.*{port}:127.0.0.1:11434"], capture_output=True)
        time.sleep(1)
        attempt = subprocess.run(forward, capture_output=True, text=True)
    if attempt.returncode:
        raise RuntimeError(f"開 SSH tunnel 失敗：{attempt.stderr.strip()}")
    for _ in range(10):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"{base_url}/api/tags", timeout=4).close()
            return
        except (OSError, urllib.error.URLError):
            continue
    raise RuntimeError("SSH tunnel 開了，但 Ollama 還是連不到")


def installed_models(base_url: str) -> set[str]:
    with urllib.request.urlopen(f"{base_url}/api/tags", timeout=8) as response:
        data = json.load(response)
    return {entry["name"] for entry in data.get("models", [])}


def sample_images(backgrounds_json: Path, per_group: int, seed: int
                   ) -> list[dict[str, Any]]:
    data = json.loads(backgrounds_json.read_text(encoding="utf-8"))
    images = data.get("images", {})
    keywords = data.get("keywords", {})
    keyword_category = {
        keyword: (bucket.get("category") or "UNCATEGORIZED")
        for keyword, bucket in keywords.items()
    }
    by_category: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for key, info in images.items():
        term = info.get("term", "")
        category = keyword_category.get(term, "UNCATEGORIZED")
        by_category[category].append((key, info))

    rng = random.Random(seed)
    sampled = []
    for category in sorted(by_category):
        items = by_category[category][:]
        rng.shuffle(items)
        for key, info in items[:per_group]:
            sampled.append({
                "key": key, "category": category, "term": info.get("term", ""),
                "file": info.get("file", ""),
            })
    return sampled


def call_model(base_url: str, model: str, image_b64: str, timeout: int
               ) -> dict[str, Any]:
    payload = json.dumps({
        "model": model,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 100},
        "messages": [{"role": "user", "content": PROMPT, "images": [image_b64]}],
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/chat", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        elapsed = time.monotonic() - start
        text = str(result.get("message", {}).get("content", "")).strip()
        if not text:
            return {"ok": False, "error": "空回覆", "seconds": round(elapsed, 2)}
        return {"ok": True, "text": text, "seconds": round(elapsed, 2)}
    except Exception as error:                                    # noqa: BLE001
        elapsed = time.monotonic() - start
        return {"ok": False, "error": f"{type(error).__name__}: {error}",
                "seconds": round(elapsed, 2)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--ollama-url", default=DEFAULT_URL)
    parser.add_argument("--ssh-target", default=DEFAULT_SSH_TARGET)
    parser.add_argument("--backgrounds-json", default=str(ROOT / "assets/backgrounds.json"))
    parser.add_argument("--project-root", default=str(ROOT),
                         help="解析 backgrounds.json 裡 file 相對路徑用")
    parser.add_argument("--per-group", type=int, default=6,
                         help="每個背景類別抽幾張圖（22 類）")
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--limit-total", type=int, default=None,
                         help="只測前 N 張抽樣圖（快速試跑用）")
    parser.add_argument("--out-dir", default=str(ROOT / "tools/model_eval/results"))
    parser.add_argument("--dry-run", action="store_true",
                         help="只列出抽樣結果，不呼叫任何模型")
    args = parser.parse_args()

    project_root = Path(args.project_root)
    sampled = sample_images(Path(args.backgrounds_json), args.per_group, args.seed)
    if args.limit_total:
        sampled = sampled[:args.limit_total]
    print(f"抽樣：{len(sampled)} 張圖，跨 "
          f"{len({item['category'] for item in sampled})} 個類別")

    if args.dry_run:
        for item in sampled:
            print(f"  [{item['category']}] {item['key']}  term={item['term']!r}")
        return

    ensure_tunnel(args.ollama_url, args.ssh_target)
    available = installed_models(args.ollama_url)
    missing = [model for model in args.models if model not in available]
    if missing:
        print(f"警告：這些模型還沒裝，會跳過：{missing}", file=sys.stderr)
    models_to_run = [model for model in args.models if model in available]
    if not models_to_run:
        raise SystemExit("沒有任何要測的模型已經裝好")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"vision_bench_{timestamp}.json"
    md_path = out_dir / f"vision_bench_{timestamp}.md"

    raw: dict[str, Any] = {
        "generated_at": timestamp,
        "prompt": PROMPT,
        "sample_size": len(sampled),
        "per_group": args.per_group,
        "seed": args.seed,
        "note": ("match 是「關鍵字裡有意義的字，模型描述裡有沒有出現」的粗略字詞"
                  "重疊比對，不是人工核對過的精確準確率。"),
        "models": {},
    }

    # 模型在外層迴圈：見檔頭說明，這是為了不要在 CPU 上重複讀模型進記憶體。
    for model_index, model in enumerate(models_to_run, start=1):
        print(f"\n=== [{model_index}/{len(models_to_run)}] {model} ===")
        entries = []
        for image_index, item in enumerate(sampled, start=1):
            image_path = project_root / item["file"]
            if not image_path.is_file():
                entries.append({**item, "ok": False, "error": "檔案不存在"})
                continue
            image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
            result = call_model(args.ollama_url, model, image_b64, args.timeout)
            entry = {**item, **result}
            if result.get("ok"):
                matched, overlap = match_score(item["term"], result["text"])
                entry["matched"] = matched
                entry["overlap"] = overlap
            entries.append(entry)
            status = "OK " if result.get("ok") else "ERR"
            print(f"  [{image_index}/{len(sampled)}] {status} "
                  f"{result.get('seconds', 0):>6.1f}s  {item['key']}")

        ok_entries = [e for e in entries if e.get("ok")]
        matched_entries = [e for e in ok_entries if e.get("matched")]
        latencies = [e["seconds"] for e in ok_entries]
        stats = {
            "attempted": len(entries),
            "succeeded": len(ok_entries),
            "failed": len(entries) - len(ok_entries),
            "match_rate_of_succeeded": (
                round(len(matched_entries) / len(ok_entries), 3) if ok_entries else None
            ),
            "match_rate_of_attempted": round(len(matched_entries) / len(entries), 3),
            "avg_latency_seconds": round(statistics.mean(latencies), 2) if latencies else None,
            "median_latency_seconds": round(statistics.median(latencies), 2) if latencies else None,
        }
        print(f"  -> {stats}")
        raw["models"][model] = {"stats": stats, "entries": entries}
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Vision model bench — background image library",
        "",
        f"生成時間：{timestamp}　樣本數：{len(sampled)}　"
        f"每類抽樣：{args.per_group} 張　seed：{args.seed}",
        "",
        raw["note"],
        "",
        "| model | succeeded/attempted | match rate (of succeeded) | avg latency (s) | median latency (s) |",
        "|---|---|---|---|---|",
    ]
    for model, payload in raw["models"].items():
        stats = payload["stats"]
        lines.append(
            f"| {model} | {stats['succeeded']}/{stats['attempted']} | "
            f"{stats['match_rate_of_succeeded']} | {stats['avg_latency_seconds']} | "
            f"{stats['median_latency_seconds']} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n寫入 {raw_path}")
    print(f"寫入 {md_path}")


if __name__ == "__main__":
    main()
