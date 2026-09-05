#!/usr/bin/env python3
"""比較 cuba001 上幾支文字模型，做「幫一段新聞內容想一個英文搜尋詞」這件事。

## 這支腳本測的是哪個 prompt

專案裡沒有一支「輸入文章、輸出關鍵字」的獨立 LLM 呼叫——`core/topic.py`
的 `keywords()` 是規則式（抓大寫字、算詞頻），不是問模型。**唯一**真的
「請模型自己想一個搜尋詞」的地方，是 `assets/prompts/visual.md` 裡的
`bg_search`：素材清單裡挑不到合用的背景圖時，請模型給一句**英文、講畫面
本身**的搜尋詞去 Pexels 現找，而不是講這句話的抽象意思（原文的反例是
"renaming controversy"——那不是一張照片會長的樣子）。

這支腳本原封不動搬那條規則，只是把輸入從「一句卡片文案」換成「一整篇新聞
正文」——問法還是同一個問法：「這篇東西該配一張什麼樣的照片，用英文講那
張照片，不要講這篇東西在講什麼道理」。系統 prompt 見 `SYSTEM_PROMPT`，
逐字保留了 visual.md 那幾句的用詞（"講畫面本身"／不要講意義／反例）。

## 文章從哪裡來

用這個專案自己已經在用、通過測試的抓法：`core/topic.py` 的
`NEWS_RSS`／`hunt_reports()` 那一套 Google News RSS 查詢格式，換成不同的
`hl`/`gl`/`ceid` 分別要英文跟中文的新聞；每一條連結交給
`core/article.py` 的 `text_of()`——它會自己處理 Google 轉址
（`real_url()`）、用 `browser_fetch()` 繞開 Cloudflare 式的擋機器人、
`trafilatura.extract()` 出正文，而且抓過的會存進 `assets/articles/`，
重跑不用重抓。這裡不重造第二套抓取邏輯。

## 這支腳本不做的事

不評斷哪個模型的關鍵字比較好——那是質化判斷，留給另一個人看
`keyword_bench_<timestamp>.json` 自己讀。這裡只收集：每個模型、每篇文章
的原始輸出、花了幾秒、有沒有機械性失敗（格式跑掉、逾時、拒答）。
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import article as article_module          # noqa: E402
from core import stock as stock_module               # noqa: E402
from core.topic import ITEM, LINK, TITLE             # noqa: E402  (現成的 regex，不重刻)

DEFAULT_MODELS = [
    "gpt-oss:120b", "gpt-oss:20b", "qwen3:32b", "qwen3:30b-a3b",
    "qwen3:8b", "qwen3:4b", "qwen2.5:7b", "qwen2.5:1.5b",
]
DEFAULT_URL = "http://127.0.0.1:11435"
DEFAULT_SSH_TARGET = "yuyu@cuba001"

NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"

# 逐字保留 assets/prompts/visual.md 裡 bg_search 那幾句的用詞，只是把「一句
# 卡片文案」換成「一整篇新聞正文」——這是專案裡唯一真的「請模型想一個搜尋
# 詞」的地方，見檔頭說明。
SYSTEM_PROMPT = (
    "你在幫一段新聞內容挑一張背景照片，素材庫裡沒有現成的圖，需要你自己想"
    "一個英文搜尋詞去圖庫（Pexels）現找。\n\n"
    "規則：\n"
    "- 只能用英文，講畫面本身（例如 \"aerial coastline sunset\"），不要講"
    "這篇報導的意思或抽象概念（不要寫 \"renaming controversy\"，那不是一張"
    "照片會長的樣子）。\n"
    "- 搜尋詞要具體到圖庫裡搜得到實際存在的照片：一個場景、幾個物件、一種"
    "氛圍，不要抽象名詞。\n"
    "- 只輸出一個英文搜尋詞（3 到 6 個字的片語），不要輸出其他文字、標點或"
    "解釋。"
)
USER_TEMPLATE = "這篇報導的內容：\n\n{body}"

# 送進模型的字數上限。文章原文可能上萬字，但 cuba001 全跑 CPU，prompt 越長
# prefill 越慢；這支腳本測的是「給一段新聞內容能不能想出一個像樣的搜尋
# 詞」，不需要整篇——跟 core/article.py 自己的 MOST_CHARS（給校對用）是
# 不同的預算，各自的用途決定各自的上限。
BODY_CHARS = 3000


def _resolve_ssl_context():
    return stock_module._ssl_context()


def discover_articles(query: str, hl: str, gl: str, ceid: str, want: int,
                      exclude_urls: set[str]) -> list[dict[str, str]]:
    """跟 `core/topic.py` 的 `hunt_reports()` 同一套 Google News RSS 查詢。"""
    link = NEWS_RSS.format(query=urllib.parse.quote(query), hl=hl, gl=gl, ceid=ceid)
    try:
        page = urllib.request.urlopen(
            urllib.request.Request(link, headers={"User-Agent": "Mozilla/5.0"}),
            timeout=25, context=_resolve_ssl_context(),
        ).read().decode("utf-8", "replace")
    except Exception as error:                                    # noqa: BLE001
        print(f"  RSS 查詢失敗（{query!r}）：{error}", file=sys.stderr)
        return []
    found = []
    for block in ITEM.findall(page):
        title_match = TITLE.search(block)
        link_match = LINK.search(block)
        if not title_match or not link_match:
            continue
        title = html.unescape(title_match.group(1))
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title).strip()
        url = link_match.group(1).strip()
        if not url or url in exclude_urls:
            continue
        found.append({"title": title, "url": url})
        if len(found) >= want:
            break
    return found


def gather(language: str, hl: str, gl: str, ceid: str, queries: list[str],
          want: int, topic: str) -> list[dict[str, Any]]:
    """抓到 `want` 篇讀得到正文的文章為止，記錄抓不到的原因（不安靜跳過）。"""
    kept: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    failures: list[dict[str, str]] = []
    for query in queries:
        if len(kept) >= want:
            break
        candidates = discover_articles(query, hl, gl, ceid, want * 3, seen_urls)
        for item in candidates:
            if len(kept) >= want:
                break
            seen_urls.add(item["url"])
            report = {"outlet": "", "title": item["title"], "url": item["url"]}
            words, why, path = article_module.text_of(topic, report)
            if not words:
                failures.append({"url": item["url"], "title": item["title"], "reason": why})
                continue
            kept.append({
                "language": language, "query": query, "title": item["title"],
                "url": item["url"], "chars": len(words),
                "cached_at": str(path.relative_to(ROOT)) if path else None,
                "body": words[:BODY_CHARS],
            })
    print(f"  {language}：收到 {len(kept)}/{want} 篇，"
          f"抓不到 {len(failures)} 篇" +
          (f"　（原因：{[f['reason'] for f in failures[:5]]}…）" if failures else ""))
    return kept


def ensure_tunnel(base_url: str, ssh_target: str) -> None:
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


def call_model(base_url: str, model: str, system: str, user: str, timeout: int
               ) -> dict[str, Any]:
    """跟 `core/writer.py` 的 `ask()` 一模一樣的呼叫形狀：`/api/generate`、單一
    `prompt` 字串、`options` 只有 `temperature` 和 `num_ctx`——沒有 `think`，
    沒有 `num_predict`。之前那版自己加了 `think: false` 和
    `num_predict: 200` 想解決「thinking 模型吐出空 content」的症狀，但正式
    程式碼從來沒有這樣呼叫過；實測 `qwen3:30b-a3b` 用這個真正的呼叫形狀，
    `response` 乾淨、`thinking` 分開放在自己的欄位，沒有互相汙染——那兩個
    加上去的選項才是原本測出「0% clean」的原因，不是模型本身的問題。
    """
    prompt = f"{system}\n\n{user}"
    payload = json.dumps({
        "model": model, "stream": False,
        "prompt": prompt,
        "options": {"temperature": 0.7, "num_ctx": 32768},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/api/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        elapsed = time.monotonic() - start
        text = str(result.get("response", "")).strip()
        if not text:
            # 跟正式程式碼一樣不主動關掉 thinking；如果模型還是把答案留在
            # thinking 欄位裡沒吐到 response，記下這件事本身（機械性失敗的
            # 一種），但如果 thinking 裡面確實有字，就當作退而求其次的答案，
            # 好過整條記錄是空的。
            thinking = str(result.get("thinking", "")).strip()
            if thinking:
                return {"ok": True, "text": thinking, "seconds": round(elapsed, 2),
                        "note": "response 是空的，這是 thinking 欄位的內容"}
            return {"ok": False, "error": "空回覆（response 和 thinking 都是空的）",
                    "seconds": round(elapsed, 2)}
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
    parser.add_argument("--want-en", type=int, default=10)
    parser.add_argument("--want-zh", type=int, default=10)
    parser.add_argument("--en-queries", nargs="+",
                        default=["world", "technology", "economy", "climate"])
    parser.add_argument("--zh-queries", nargs="+",
                        default=["國際", "財經", "科技", "社會"])
    parser.add_argument("--topic-en", default="modeleval-en")
    parser.add_argument("--topic-zh", default="modeleval-zh")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="每次呼叫的逾時秒數；gpt-oss:120b 在純 CPU 上一次"
                             "呼叫可能要好幾分鐘，這裡給到 30 分鐘的餘裕，"
                             "不要提早放棄")
    parser.add_argument("--out-dir", default=str(ROOT / "tools/model_eval/results"))
    parser.add_argument("--articles-only", action="store_true",
                        help="只抓文章存檔，不呼叫任何模型（先看抓到什麼）")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="不重新抓文章，只用 assets/articles/ 已經存的（需搭配 --topic-*）")
    args = parser.parse_args()

    if args.skip_fetch:
        articles: list[dict[str, Any]] = []
        for topic, language in ((args.topic_en, "en"), (args.topic_zh, "zh")):
            folder = ROOT / "assets" / "articles" / topic
            for path in sorted(folder.glob("*.txt")) if folder.is_dir() else []:
                text = path.read_text(encoding="utf-8")
                header, _, body = text.partition("\n\n")
                lines = header.splitlines()
                url = lines[1][2:].strip() if len(lines) > 1 else ""
                articles.append({
                    "language": language, "query": "", "title": lines[0][2:].strip() if lines else "",
                    "url": url, "chars": len(body), "cached_at": str(path.relative_to(ROOT)),
                    "body": body[:BODY_CHARS],
                })
        print(f"用快取：{len(articles)} 篇")
    else:
        print("抓英文文章…")
        en_articles = gather("en", "en-US", "US", "US:en", args.en_queries,
                             args.want_en, args.topic_en)
        print("抓中文文章…")
        zh_articles = gather("zh", "zh-TW", "TW", "TW:zh-Hant", args.zh_queries,
                             args.want_zh, args.topic_zh)
        articles = en_articles + zh_articles

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"keyword_bench_{timestamp}.json"

    raw: dict[str, Any] = {
        "generated_at": timestamp,
        "system_prompt": SYSTEM_PROMPT,
        "note": ("system_prompt 逐字沿用 assets/prompts/visual.md 的 bg_search 規則"
                  "（專案裡唯一真的請模型想搜尋詞的地方），只是輸入換成一整篇新聞"
                  "正文；沒有評斷任何模型輸出的好壞，只記錄原始輸出、花費時間、"
                  "機械性失敗。"),
        "articles": [{k: v for k, v in a.items() if k != "body"} for a in articles],
        "models": {},
    }
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n共 {len(articles)} 篇文章，寫入 {raw_path}（先存文章清單）")

    if args.articles_only:
        return

    ensure_tunnel(args.ollama_url, args.ssh_target)
    available = installed_models(args.ollama_url)
    missing = [model for model in args.models if model not in available]
    if missing:
        print(f"警告：這些模型還沒裝，會跳過：{missing}", file=sys.stderr)
    models_to_run = [model for model in args.models if model in available]
    if not models_to_run:
        raise SystemExit("沒有任何要測的模型已經裝好")

    for model_index, model in enumerate(models_to_run, start=1):
        print(f"\n=== [{model_index}/{len(models_to_run)}] {model} ===")
        entries = []
        for article_index, article in enumerate(articles, start=1):
            user = USER_TEMPLATE.format(body=article["body"])
            result = call_model(args.ollama_url, model, SYSTEM_PROMPT, user, args.timeout)
            entry = {
                "url": article["url"], "title": article["title"],
                "language": article["language"], **result,
            }
            entries.append(entry)
            status = "OK " if result.get("ok") else "ERR"
            reply = result.get("text", result.get("error", ""))
            print(f"  [{article_index}/{len(articles)}] {status} "
                  f"{result.get('seconds', 0):>6.1f}s  {article['language']}  "
                  f"{reply[:60]!r}")
        ok_entries = [e for e in entries if e.get("ok")]
        latencies = [e["seconds"] for e in ok_entries]
        stats = {
            "attempted": len(entries),
            "succeeded": len(ok_entries),
            "failed": len(entries) - len(ok_entries),
            "avg_latency_seconds": (
                round(sum(latencies) / len(latencies), 2) if latencies else None
            ),
        }
        print(f"  -> {stats}")
        raw["models"][model] = {"stats": stats, "entries": entries}
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n寫入 {raw_path}")


if __name__ == "__main__":
    main()
