"""Fill the shared background pool from a fixed, planned list of keywords.

Every keyword a card's `bg_search` has ever asked for got here by accident --
someone wrote a line, the line needed a mood shot, Pexels got searched once,
and the keyword stuck. That works, but it means the pool's shape is whatever
the last few scripts happened to need, not a considered set that will already
have an answer for the next one.

This script goes the other way: `assets/background_categories.json` names 21
categories (Adobe Stock's own top-level taxonomy) and a dozen-ish concrete,
photographable keywords under each -- about 250 in total -- and this fills
every one of them to `backgrounds.MAX_PER_KEYWORD` images up front, tagged
with its category so the studio page can browse by category first and
keywords second, instead of one long list sorted by how popular a term
happens to be.

    python tools/build_backgrounds.py                  fill every keyword
    python tools/build_backgrounds.py --only Business   just one category
    python tools/build_backgrounds.py --dry-run         list what would run

Safe to stop and re-run: `backgrounds.ensure()` skips a keyword that is
already at MAX_PER_KEYWORD, so a re-run only does the ones left over from a
run that was interrupted or that failed on a handful of keywords Pexels had
nothing for.

This only ever builds the "general" set -- see `backgrounds.tag_specific_set()`
for the not-yet-used other half of this: picking a narrower set of these same
keywords for one kind of topic, once there is a real caller that needs it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Run as a script, this file's own directory is on the path and the
# repository root is not. core/ lives at the root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import backgrounds  # noqa: E402

CATEGORIES = ROOT / "assets" / "background_categories.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default="",
                         help="只做類別名稱包含這個字串的（例如 Business）")
    parser.add_argument("--dry-run", action="store_true",
                         help="只列出會跑哪些類別／關鍵字，不真的打 Pexels")
    args = parser.parse_args()

    plan = json.loads(CATEGORIES.read_text(encoding="utf-8"))
    if args.only:
        plan = {name: words for name, words in plan.items()
                if args.only.lower() in name.lower()}
        if not plan:
            print(f"沒有類別名稱包含「{args.only}」")
            return

    total_keywords = sum(len(words) for words in plan.values())
    print(f"{len(plan)} 類，共 {total_keywords} 個關鍵字，"
          f"每個補到 {backgrounds.MAX_PER_KEYWORD} 張")
    if args.dry_run:
        for name, words in plan.items():
            print(f"\n## {name}（{len(words)}）")
            print("、".join(words))
        return

    # 失敗算常態，不算例外：Pexels 對某些詞可能真的沒有合適的圖，一個
    # 詞失敗不該讓其餘 249 個詞也跑不完。每一個都算，最後把失敗的名字
    # 講出來，不是安靜地少幾個。
    done, short, failed = 0, [], []
    started = time.time()
    for category, words in plan.items():
        print(f"\n## {category}")
        for keyword in words:
            try:
                kept = backgrounds.ensure(keyword, category=category,
                                          tag_set="general")
            except Exception as error:                                # noqa: BLE001
                print(f"  ✗ {keyword}：{error}")
                failed.append(keyword)
                continue
            done += 1
            mark = "✓" if kept >= backgrounds.MAX_PER_KEYWORD else "△"
            print(f"  {mark} {keyword}　{kept}/{backgrounds.MAX_PER_KEYWORD}")
            if kept < backgrounds.MAX_PER_KEYWORD:
                short.append(f"{keyword}（{kept}）")

    took = time.time() - started
    print(f"\n完成 {done}/{total_keywords} 個關鍵字，花了 {took / 60:.1f} 分鐘")
    if short:
        print(f"⚠ {len(short)} 個沒補滿 {backgrounds.MAX_PER_KEYWORD} 張"
              f"（Pexels 給得出的就這麼多）：{'、'.join(short)}")
    if failed:
        print(f"⚠ {len(failed)} 個整個失敗：{'、'.join(failed)}")


if __name__ == "__main__":
    main()
