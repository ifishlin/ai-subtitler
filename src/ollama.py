from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any


class OllamaClient:
    def __init__(self, base_url: str, model: str, ssh_target: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.ssh_target = ssh_target

    def _get_tags(self) -> dict[str, Any]:
        with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=4) as response:
            return json.load(response)

    def ensure_ready(self) -> None:
        try:
            tags = self._get_tags()
        except (OSError, urllib.error.URLError):
            if not self.ssh_target:
                raise RuntimeError("Ollama unavailable and no SSH target configured")
            port = self.base_url.rsplit(":", 1)[-1]
            subprocess.run([
                "ssh", "-f", "-N", "-L", f"{port}:127.0.0.1:11434",
                "-o", "ExitOnForwardFailure=yes", self.ssh_target,
            ], check=True)
            for _ in range(10):
                time.sleep(1)
                try:
                    tags = self._get_tags()
                    break
                except (OSError, urllib.error.URLError):
                    continue
            else:
                raise RuntimeError("SSH tunnel opened, but Ollama is unreachable")

        models = {entry["name"] for entry in tags.get("models", [])}
        if self.model not in models:
            raise RuntimeError(f"Configured model {self.model!r} is unavailable; found: {sorted(models)}")

    def chat_json(self, system: str, user: str, timeout: int = 300) -> dict[str, Any]:
        payload = json.dumps({
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode()
        request = urllib.request.Request(
            f"{self.base_url}/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
        content = result["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(content)
