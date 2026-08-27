"""Choosing which language model does the reading.

The pipeline asks a model to proofread, translate, review and plan cards. Which
model that is should be a flag, not a rewrite: the steps themselves only need
ensure_ready() and chat_json(), and every reply is validated by the caller
regardless of who answered. A stronger model changes how often the validation
rejects something, not whether it runs.

Providers are registered here rather than chosen with an if-chain, so adding
one is a line and the command line's choices stay honest -- --llm lists what
actually exists.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

# Every provider: how to build it, and a sentence for --help.
PROVIDERS: dict[str, dict[str, Any]] = {}


def provider(name: str, blurb: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def keep(build: Callable[..., Any]) -> Callable[..., Any]:
        PROVIDERS[name] = {"build": build, "blurb": blurb}
        return build
    return keep


@provider("qwen", "本機／cuba001 上的 Ollama，免費，品質勉強")
def _qwen(settings: dict[str, Any]) -> Any:
    from .ollama import OllamaClient
    return OllamaClient(settings["ollama_url"], settings["ollama_model"],
                        settings["ssh_target"])


@provider("claude", "Anthropic API，要金鑰，校對與翻譯明顯較好")
def _claude(settings: dict[str, Any]) -> Any:
    from .claude import ClaudeClient
    kwargs: dict[str, Any] = {"spec": settings.get("claude")}
    if settings.get("model"):
        kwargs["model"] = settings["model"]
    if settings.get("effort"):
        kwargs["effort"] = settings["effort"]
    return ClaudeClient(**kwargs)


@provider("none", "不呼叫任何模型：辨識與燒錄照跑，校對翻譯圖卡跳過")
def _none(settings: dict[str, Any]) -> None:
    return None


@provider("replay", "重播 --llm-log 錄下的回覆，用來離線測試流程，不花錢")
def _replay(settings: dict[str, Any]) -> Any:
    return ReplayClient(Path(settings["replay_from"]))


class Usage:
    """What the run spent. Kept per client so a step can be blamed for its own
    cost rather than the total appearing at the end as a surprise."""

    def __init__(self) -> None:
        self.calls = 0
        self.input = 0
        self.output = 0

    def add(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        self.calls += 1
        self.input += int(getattr(usage, "input_tokens", 0) or 0)
        self.output += int(getattr(usage, "output_tokens", 0) or 0)

    def line(self) -> str:
        if not self.calls:
            return "沒有呼叫語言模型"
        return (f"語言模型呼叫 {self.calls} 次，"
                f"輸入 {self.input:,} token，輸出 {self.output:,} token")


class ReplayClient:
    """Answers from a recorded log instead of a network.

    Building the stages that read a transcript should not need a paid key, or a
    tunnel to a machine in another country. A recorded run replays exactly, so
    the code around the model can be worked on and tested offline; an unseen
    question is an error rather than an invention, because a stub that makes
    something up would prove the wrong thing.
    """

    def __init__(self, log: Path):
        self.answers = json.loads(log.read_text(encoding="utf-8")) if log.is_file() else []
        self.at = 0

    def ensure_ready(self) -> None:
        if not self.answers:
            raise RuntimeError("錄影檔是空的，沒有可以重播的回覆")

    def chat_json(self, system: str, user: str, timeout: int = 300,
                  schema: dict[str, Any] | None = None) -> dict[str, Any]:
        # Match on the question when the log records it, so reordering a
        # pipeline does not silently hand a step the previous step's answer.
        for entry in self.answers:
            if entry.get("user") == user:
                return entry["reply"]
        if self.at < len(self.answers):
            entry = self.answers[self.at]
            self.at += 1
            return entry["reply"]
        raise RuntimeError("錄影檔沒有這一題的回覆")


class Recorder:
    """Wraps a client and writes every exchange to a file, so a paid run can be
    replayed for free while the code around it is worked on."""

    def __init__(self, inner: Any, log: Path):
        self.inner = inner
        self.log = log
        self.entries: list[dict[str, Any]] = []

    def ensure_ready(self) -> None:
        self.inner.ensure_ready()

    def chat_json(self, system: str, user: str, timeout: int = 300,
                  schema: dict[str, Any] | None = None) -> dict[str, Any]:
        kwargs = {"timeout": timeout}
        if schema is not None:
            kwargs["schema"] = schema
        reply = self.inner.chat_json(system, user, **kwargs)
        self.entries.append({"system": system, "user": user, "reply": reply})
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return reply

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def build(name: str, settings: dict[str, Any]) -> Any:
    """The client for the requested provider, or None to run without one."""
    if name not in PROVIDERS:
        raise SystemExit(f"不認得的 --llm {name}；可用的有：{', '.join(PROVIDERS)}")
    client = PROVIDERS[name]["build"](settings)
    if client is not None and settings.get("record_to"):
        client = Recorder(client, Path(settings["record_to"]))
    return client


def choices() -> list[str]:
    return list(PROVIDERS)


def help_text() -> str:
    return "；".join(f"{name}＝{spec['blurb']}" for name, spec in PROVIDERS.items())
