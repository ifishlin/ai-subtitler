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


@provider("ask", "把每一題寫進 --llm-log 讓人回答，這次不做任何修改")
def _ask(settings: dict[str, Any]) -> Any:
    if not settings.get("record_to"):
        raise SystemExit("--llm ask 需要 --llm-log 指定題目要寫到哪裡")
    return AskClient(Path(settings["record_to"]))


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
        self.unanswered = 0

    def ensure_ready(self) -> None:
        if not self.answers:
            raise RuntimeError("錄影檔是空的，沒有可以重播的回覆")

    def chat_json(self, system: str, user: str, timeout: int = 300,
                  schema: dict[str, Any] | None = None) -> dict[str, Any]:
        # Matched on the question itself. Answering by position would hand a
        # step the previous step's answer the moment anything upstream changes
        # what it asks -- and correcting a transcript changes what the
        # translator is shown, which is exactly when this gets used.
        for entry in self.answers:
            if entry.get("user") == user and entry.get("reply") is not None:
                return entry["reply"]
        # Unanswered is not an error: answering a run in rounds means the later
        # rounds' questions do not exist yet. It has to be said out loud,
        # though, or a half-finished log looks like a finished one.
        self.unanswered += 1
        print(f"      （這一題沒有錄到答案，當作沒有修改：{user[:40]}…）")
        return AskClient._nothing(user)


class AskClient:
    """Writes down what would have been asked, and changes nothing.

    Answering as the model yourself needs the questions first, and the
    questions are built deep inside the pipeline -- batched, with the video's
    description folded in. Rather than reconstruct them, this runs the real
    thing and records each one, replying that there is nothing to change so
    the run completes and asks everything it would have asked.

    The empty reply is deliberately shaped as "no edits, no cuts, no cards":
    every caller reads its own key with a default, so `{}` leaves the
    transcript exactly as recognition produced it.
    """

    def __init__(self, log: Path):
        self.log = log
        self.entries: list[dict[str, Any]] = []

    def ensure_ready(self) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)

    def chat_json(self, system: str, user: str, timeout: int = 300,
                  schema: dict[str, Any] | None = None) -> dict[str, Any]:
        self.entries.append({"system": system, "user": user, "reply": None})
        self.log.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        return self._nothing(user)

    @staticmethod
    def _nothing(user: str) -> dict[str, Any]:
        """A reply meaning "no change" in every shape the pipeline asks for.

        A bare {} looked like a dropped batch to the translator, which splits
        and retries down to single lines -- 19 questions became 159, most of
        them duplicates of a question already recorded. Echoing the ids back
        empty is what a model that had nothing to say would return, so the run
        asks exactly what it would really ask.
        """
        reply: dict[str, Any] = {"edits": [], "hallucinated": [], "findings": [],
                                 "visuals": []}
        start = user.find('{"segments"')
        if start >= 0:
            try:
                sent = json.loads(user[start:])
                reply["segments"] = [{"id": item["id"], "text": ""}
                                     for item in sent.get("segments", [])]
            except (ValueError, KeyError, TypeError):
                pass
        return reply


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
