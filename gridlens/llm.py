"""Thin LLM provider layer. Every provider takes a system and a user message and returns text.

Ollama runs locally (a laptop GPU is enough for an 8B model), so the tool works
with no API key. Anthropic and OpenAI are drop-in alternatives.
"""
from __future__ import annotations

import json
import os
from typing import Protocol

import httpx

from .models import IncidentSummary


class Provider(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


SCHEMA = IncidentSummary.model_json_schema()


class OllamaProvider:
    def __init__(self, model: str | None = None, host: str | None = None, timeout: float = 120):
        self.model = model or os.getenv("GRIDLENS_OLLAMA_MODEL", "llama3.1:8b")
        self.host = (host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.timeout = timeout
        self.name = f"ollama:{self.model}"

    def complete(self, system: str, user: str) -> str:
        r = httpx.post(
            f"{self.host}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "format": SCHEMA,  # Ollama constrains decoding to this JSON schema
                "options": {"temperature": 0},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]


class AnthropicProvider:
    def __init__(self, model: str | None = None, timeout: float = 60):
        self.model = model or os.getenv("GRIDLENS_ANTHROPIC_MODEL", "claude-haiku-4-5")
        self.key = os.environ["ANTHROPIC_API_KEY"]
        self.timeout = timeout
        self.name = f"anthropic:{self.model}"

    def complete(self, system: str, user: str) -> str:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"},
            json={
                "model": self.model,
                "max_tokens": 1024,
                "temperature": 0,
                "system": system + "\nRespond with only the JSON object, no prose.",
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"])


class OpenAIProvider:
    def __init__(self, model: str | None = None, base_url: str | None = None, timeout: float = 60):
        self.model = model or os.getenv("GRIDLENS_OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.key = os.environ["OPENAI_API_KEY"]
        self.timeout = timeout
        self.name = f"openai:{self.model}"
        # OpenAI uses `json_schema`; llama.cpp's server takes the schema inside `json_object`.
        self.json_mode = os.getenv("GRIDLENS_OPENAI_JSON_MODE", "json_schema")

    def _response_format(self) -> dict:
        if self.json_mode == "llamacpp":
            return {"type": "json_object", "schema": SCHEMA}
        if self.json_mode == "json_object":
            return {"type": "json_object"}
        return {"type": "json_schema", "json_schema": {"name": "incident_summary", "schema": SCHEMA}}

    def complete(self, system: str, user: str) -> str:
        r = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "response_format": self._response_format(),
                "messages": [
                    {"role": "system", "content": system + "\nReturn JSON matching this schema:\n" + json.dumps(SCHEMA)},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class ScriptedProvider:
    """Returns canned responses in order. Used by tests to exercise the repair loop."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.name = "scripted"

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.responses.pop(0)


def get_provider(spec: str) -> Provider | None:
    """`ollama`, `ollama:qwen2.5:7b`, `anthropic`, `openai`, or `baseline` (returns None)."""
    kind, _, model = spec.partition(":")
    if kind == "baseline":
        return None
    if kind == "ollama":
        return OllamaProvider(model or None)
    if kind == "anthropic":
        return AnthropicProvider(model or None)
    if kind == "openai":
        return OpenAIProvider(model or None)
    raise ValueError(f"Unknown provider: {spec}")
