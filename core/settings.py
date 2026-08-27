"""One place the run reads its settings from.

Which provider answers, which model, where the key lives, how to reach the
machine with the GPU-less cores -- these were spread across defaults in
produce.py, a constant in claude.py and a filename in the home directory. One
file now holds them, outside the repository, because half of them are secrets
and the other half are this machine's business rather than the project's.

Four sources, each beating the one before it:

    built-in defaults  <  config file  <  environment  <  command line

so a config can set the habit and a flag can override it for one run, which is
the order people actually expect.

Secrets are never written here. A provider names a file to read its key from,
or an environment variable holding it; the value itself stays where it was.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

HOME = Path.home() / ".config" / "video_pipeline"
CONFIG = Path(os.environ.get("VIDEO_PIPELINE_CONFIG") or HOME / "config.toml")

DEFAULTS: dict[str, Any] = {
    "llm": {
        "provider": "qwen",
        "claude": {"model": "claude-opus-5", "effort": "medium",
                   "key_file": str(HOME / "anthropic"),
                   "key_env": "ANTHROPIC_API_KEY"},
        "qwen": {"url": "http://127.0.0.1:11435", "model": "qwen2.5:7b",
                 "ssh_target": ""},
    },
    "stock": {"pexels": {"key_file": str(HOME / "pexels")}},
}


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Deep merge, so a config naming one field keeps the defaults of the rest."""
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: Path | None = None) -> dict[str, Any]:
    """The settings, defaults filled in. A missing file is not an error: the
    defaults are a working configuration for the local model."""
    path = path or CONFIG
    if not path.is_file():
        return DEFAULTS
    with path.open("rb") as handle:
        return _merge(DEFAULTS, tomllib.load(handle))


def secret(spec: dict[str, Any], what: str) -> str:
    """A key, from wherever this provider says it lives.

    Order matters: an environment variable wins, because that is how a key is
    supplied for one run without editing anything. `key` inline in the config
    is accepted but discouraged -- a config file gets copied around, and the
    file it points at can be locked down on its own.
    """
    env = spec.get("key_env")
    if env and os.environ.get(env):
        return os.environ[env].strip()
    if spec.get("key"):
        return str(spec["key"]).strip()
    file = spec.get("key_file")
    if file:
        path = Path(str(file)).expanduser()
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    raise RuntimeError(
        f"找不到 {what} 的金鑰。可以設環境變數 {env or 'KEY'}，"
        f"或把金鑰存進 {spec.get('key_file') or HOME}，"
        f"或在 {CONFIG} 裡指定 key_file。"
    )


def llm_options(args: Any) -> tuple[str, dict[str, Any]]:
    """The provider to use and the settings to build it with.

    Command-line flags are only allowed to speak when they were actually given:
    argparse defaults would otherwise silently overrule the config file, which
    is the usual way this kind of layering goes wrong.
    """
    config = load()
    llm = config.get("llm", {})
    claude = llm.get("claude", {})
    qwen = llm.get("qwen", {})

    provider = getattr(args, "llm", None) or llm.get("provider", "qwen")
    settings = {
        "ollama_url": getattr(args, "ollama_url", None) or qwen.get("url"),
        "ollama_model": getattr(args, "ollama_model", None) or qwen.get("model"),
        "ssh_target": getattr(args, "ssh_target", None) or qwen.get("ssh_target", ""),
        "model": getattr(args, "llm_model", None) or claude.get("model"),
        "effort": getattr(args, "llm_effort", None) or claude.get("effort"),
        "record_to": getattr(args, "llm_log", None),
        "replay_from": getattr(args, "replay_from", None) or getattr(args, "llm_log", None),
        "claude": claude,
        "config": config,
    }
    return provider, settings


def describe() -> str:
    """What is configured, without printing anything secret."""
    config = load()
    llm = config.get("llm", {})
    lines = [f"設定檔　{CONFIG}" + ("" if CONFIG.is_file() else "（不存在，使用預設值）")]
    lines.append(f"提供者　{llm.get('provider')}")
    for name, spec in sorted(llm.items()):
        if not isinstance(spec, dict):
            continue
        shown = {key: value for key, value in spec.items() if key != "key"}
        if "key" in spec:
            shown["key"] = "（設在設定檔裡）"
        found = ""
        if name == "claude":
            try:
                secret(spec, name)
                found = "　金鑰：有"
            except RuntimeError:
                found = "　金鑰：找不到"
        lines.append(f"  {name}　{shown}{found}")
    return "\n".join(lines)
