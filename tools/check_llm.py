"""Prove the language model is reachable, before a run depends on it.

Recognition takes minutes and happens first. Discovering there that a key is
missing or a tunnel is down wastes all of it, so this asks the same questions
the pipeline will -- a plain round trip, then a JSON reply against a schema --
and says plainly which part failed.

    python tools/check_llm.py --llm claude
    python tools/check_llm.py --llm qwen --ssh-target yuyu@cuba001
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Run as a script, this file's own directory is on the path and the repository
# root is not. core/ lives at the root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import llm, settings          # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "language": {"type": "string"},
    },
    "required": ["ok", "language"],
    "additionalProperties": False,
}

SYSTEM = (
    "You verify a connection. Reply with JSON only: "
    '{"ok": true, "language": "<the language of the user message>"}'
)


def main() -> int:
    parser = argparse.ArgumentParser(description="檢查語言模型連得上、答得出 JSON")
    parser.add_argument("--llm", choices=llm.choices())
    parser.add_argument("--llm-model")
    parser.add_argument("--llm-effort", choices=["low", "medium", "high"])
    parser.add_argument("--ollama-url")
    parser.add_argument("--ollama-model")
    parser.add_argument("--ssh-target")
    parser.add_argument("--replay-from")
    args = parser.parse_args()

    provider, options = settings.llm_options(args)
    print(settings.describe())
    print()
    client = llm.build(provider, options)
    if client is None:
        print(f"{provider}：不呼叫模型，沒有東西要檢查")
        return 0

    started = time.time()
    try:
        client.ensure_ready()
    except Exception as error:                                    # noqa: BLE001
        print(f"連線失敗　{error}")
        return 1
    print(f"連線成功　{time.time() - started:.1f} 秒")

    started = time.time()
    try:
        # The pipeline always wants JSON back, so a connection that cannot
        # produce it is not usable, however healthy it looks.
        reply = client.chat_json(SYSTEM, "這是一句中文。", timeout=60, schema=SCHEMA)
    except TypeError:
        reply = client.chat_json(SYSTEM, "這是一句中文。", timeout=60)   # no schema support
    except Exception as error:                                    # noqa: BLE001
        print(f"JSON 回覆失敗　{error}")
        return 1
    print(f"JSON 回覆　{reply}　{time.time() - started:.1f} 秒")

    spent = getattr(client, "usage", None)
    if spent is not None:
        print(f"用量　　　{spent.line()}")
    print("可以跑 pipeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
