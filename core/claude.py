"""Claude as the pipeline's language model, interchangeable with the local one.

Presents the same surface as OllamaClient -- ensure_ready() and chat_json() --
so proofreading, translation and card planning call it without knowing which
model answered. The validation those steps apply to a reply is unchanged: a
stronger model still only returns edits that have to be verified.

The key is read from ANTHROPIC_API_KEY or ~/.config/video_pipeline/anthropic,
never from the repository.
"""
from __future__ import annotations

import json
from typing import Any

DEFAULT_MODEL = "claude-opus-5"
MAX_TOKENS = 16000
# Proofreading and translation are judgement over short text, not open-ended
# reasoning, so medium effort is the sensible default; raise it per call for
# work where correctness matters more than cost.
DEFAULT_EFFORT = "medium"


def _key(spec: dict[str, Any] | None = None) -> str:
    """Where the key lives is a setting like any other; see core/settings.py."""
    from . import settings
    spec = spec if spec is not None else settings.load()["llm"]["claude"]
    return settings.secret(spec, "Anthropic")


class ClaudeClient:
    """Drop-in replacement for OllamaClient, backed by the Claude API."""

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
                 spec: dict[str, Any] | None = None):
        from .llm import Usage
        self.model = model
        self.effort = effort
        self.spec = spec
        self.usage = Usage()          # what this run has spent, so far
        self._client: Any = None

    def ensure_ready(self) -> None:
        """Fail here, before the expensive stages, if the key is unusable."""
        import anthropic

        self._client = anthropic.Anthropic(api_key=_key(self.spec))
        # A single cheap call proves the key works and the model is reachable.
        self._client.messages.create(
            model=self.model,
            max_tokens=16,
            messages=[{"role": "user", "content": "reply with ok"}],
        )

    def chat_json(
        self,
        system: str,
        user: str,
        timeout: int = 300,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ask for JSON and return it parsed.

        A schema, where the caller has one, is enforced by the API rather than
        requested in prose, so a reply cannot come back unparseable.
        """
        if self._client is None:
            self.ensure_ready()

        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": self.effort},
        }
        if schema:
            request["output_config"]["format"] = {
                "type": "json_schema", "schema": schema,
            }

        response = self._client.with_options(timeout=float(timeout)).messages.create(**request)
        self.usage.add(response)
        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "explanation", "") or ""
            raise RuntimeError(f"Claude 拒絕這個請求：{detail}")

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
