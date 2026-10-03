"""LLM + embedding providers behind one small interface.

LLM_PROVIDER: anthropic (Messages API) | openai (any OpenAI-compatible /chat/completions) | none
EMBED_PROVIDER: openai (/embeddings) | none
All HTTP goes through httpx; pass `transport=` to inject a mock in tests.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("app.llm")

EMBED_DIM = 1536


class LLMError(Exception):
    def __init__(self, message: str, input_tokens: int = 0, output_tokens: int = 0):
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


@dataclass
class LLMResult:
    data: dict[str, Any]
    input_tokens: int
    output_tokens: int
    raw_text: str


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from model text, tolerating code fences / leading prose."""
    t = _FENCE.sub("", text.strip())
    try:
        val = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object in model output") from None
        val = json.loads(t[start : end + 1])
    if not isinstance(val, dict):
        raise ValueError("model output is not a JSON object")
    return val


class LLMProvider:
    name = "base"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.model = settings.llm_model
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return True

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.settings.llm_timeout_seconds, transport=self._transport)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        s = self.settings
        return round(input_tokens * s.llm_input_cost_per_mtok / 1e6 + output_tokens * s.llm_output_cost_per_mtok / 1e6, 6)

    def complete_json(self, system: str, prompt: str, max_tokens: int | None = None) -> LLMResult:
        raise NotImplementedError


class NoneProvider(LLMProvider):
    name = "none"

    @property
    def enabled(self) -> bool:
        return False

    def complete_json(self, system: str, prompt: str, max_tokens: int | None = None) -> LLMResult:
        raise LLMError("LLM provider is 'none'")


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def complete_json(self, system: str, prompt: str, max_tokens: int | None = None) -> LLMResult:
        base = (self.settings.llm_base_url or "https://api.anthropic.com").rstrip("/")
        body = {
            "model": self.model,
            "max_tokens": max_tokens or self.settings.llm_max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        headers = {
            "x-api-key": self.settings.llm_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            with self._client() as c:
                r = c.post(f"{base}/v1/messages", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(f"anthropic transport error: {e}") from None
        if r.status_code != 200:
            raise LLMError(f"anthropic HTTP {r.status_code}: {r.text[:300]}")
        payload = r.json()
        usage = payload.get("usage") or {}
        itok, otok = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        text = "".join(b.get("text", "") for b in payload.get("content") or [] if b.get("type") == "text")
        try:
            return LLMResult(parse_json_object(text), itok, otok, text)
        except ValueError as e:
            raise LLMError(f"unparseable model output: {e}", itok, otok) from None


class OpenAIProvider(LLMProvider):
    name = "openai"

    def complete_json(self, system: str, prompt: str, max_tokens: int | None = None) -> LLMResult:
        base = (self.settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens or self.settings.llm_max_output_tokens,
            "temperature": 0,
        }
        headers = {"authorization": f"Bearer {self.settings.llm_api_key}"}
        try:
            with self._client() as c:
                r = c.post(f"{base}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise LLMError(f"openai transport error: {e}") from None
        if r.status_code != 200:
            raise LLMError(f"openai HTTP {r.status_code}: {r.text[:300]}")
        payload = r.json()
        usage = payload.get("usage") or {}
        itok, otok = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        try:
            text = payload["choices"][0]["message"]["content"] or ""
            return LLMResult(parse_json_object(text), itok, otok, text)
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMError(f"unparseable model output: {e}", itok, otok) from None


def make_llm(settings: Settings, transport: httpx.BaseTransport | None = None) -> LLMProvider:
    cls = {"anthropic": AnthropicProvider, "openai": OpenAIProvider}.get(settings.llm_provider, NoneProvider)
    return cls(settings, transport)


# ------------------------------------------------------------------ embeddings
class Embedder:
    name = "none"
    model = ""

    @property
    def enabled(self) -> bool:
        return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise LLMError("embeddings disabled")


class OpenAIEmbedder(Embedder):
    name = "openai"

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.model = settings.embed_model
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return True

    def embed(self, texts: list[str]) -> list[list[float]]:
        base = (self.settings.embed_base_url or "https://api.openai.com/v1").rstrip("/")
        key = self.settings.embed_api_key or self.settings.llm_api_key
        body: dict[str, Any] = {"model": self.model, "input": texts}
        if self.model.startswith("text-embedding-3"):
            body["dimensions"] = EMBED_DIM
        try:
            with httpx.Client(timeout=30, transport=self._transport) as c:
                r = c.post(f"{base}/embeddings", json=body, headers={"authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            raise LLMError(f"embedding transport error: {e}") from None
        if r.status_code != 200:
            raise LLMError(f"embedding HTTP {r.status_code}: {r.text[:300]}")
        vecs = [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
        for v in vecs:
            if len(v) != EMBED_DIM:
                raise LLMError(f"embedding dimension {len(v)} != {EMBED_DIM}")
        return vecs


def make_embedder(settings: Settings, transport: httpx.BaseTransport | None = None) -> Embedder:
    if settings.embed_provider == "openai":
        return OpenAIEmbedder(settings, transport)
    return Embedder()


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"
