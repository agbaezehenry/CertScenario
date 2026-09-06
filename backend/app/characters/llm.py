"""LLM provider abstraction (spec §6). Phase 4 builds the character engine on this.

Only the protocol and an OpenAI-compatible HTTP client live here; no
provider-specific code may appear elsewhere in the application.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from app.models.domain import CharacterContext


@dataclass(frozen=True)
class Message:
    role: str  # system | user | assistant
    content: str


class LLMProvider(Protocol):
    async def generate(self, messages: list[Message], context: CharacterContext) -> str: ...


class OpenAICompatibleProvider:
    """Minimal chat-completions client. Deterministic settings for leak tests."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None, *, temperature: float = 0.0, seed: int | None = 7) -> None:
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "gpt-4o-mini")
        self.temperature = temperature
        self.seed = seed
        self.total_tokens = 0  # spec §41: track usage

    async def generate(self, messages: list[Message], context: CharacterContext) -> str:
        import asyncio

        return await asyncio.get_running_loop().run_in_executor(None, self._call, messages)

    def _call(self, messages: list[Message]) -> str:
        body: dict[str, object] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
        }
        if self.seed is not None:
            body["seed"] = self.seed
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        self.total_tokens += int(data.get("usage", {}).get("total_tokens", 0))
        return str(data["choices"][0]["message"]["content"])


class ScriptedProvider:
    """Test double: returns canned replies in order."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    async def generate(self, messages: list[Message], context: CharacterContext) -> str:
        self.calls.append(messages)
        return self._replies.pop(0) if self._replies else ""


def provider_from_env() -> LLMProvider | None:
    if not os.environ.get("LLM_API_KEY"):
        return None
    return OpenAICompatibleProvider()
