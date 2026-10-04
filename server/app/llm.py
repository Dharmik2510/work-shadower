"""LLM + embedding providers behind one small interface.

LLM_PROVIDER: anthropic (Messages API) | openai (any OpenAI-compatible /chat/completions) | none
EMBED_PROVIDER: openai (/embeddings) | none
All HTTP goes through httpx; pass `transport=` to inject a mock in tests.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
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

    supports_batch = False

    def complete_json(self, system: str, prompt: str, max_tokens: int | None = None,
                      schema: dict | None = None) -> LLMResult:
        raise NotImplementedError

    # ------------------------------------------------------------ resilience
    _sleep = staticmethod(time.sleep)

    def _post(self, client: httpx.Client, url: str, body: dict, headers: dict, what: str) -> httpx.Response:
        """POST with retries on 408/409/429/5xx/529 and transport errors (exponential backoff + jitter,
        honouring Retry-After). Non-retryable statuses are returned to the caller."""
        attempts = max(0, self.settings.llm_max_retries) + 1
        for attempt in range(attempts):
            retry_after = None
            try:
                r = client.post(url, json=body, headers=headers)
            except httpx.HTTPError as e:
                if attempt + 1 >= attempts:
                    raise LLMError(f"{what} transport error: {e}") from None
            else:
                if r.status_code not in _RETRYABLE or attempt + 1 >= attempts:
                    return r
                retry_after = _retry_after(r.headers.get("retry-after"))
                log.warning("llm retryable status", extra={"status": r.status_code, "attempt": attempt + 1})
            delay = self.settings.llm_retry_base_seconds * (2 ** attempt) * random.uniform(0.8, 1.2)
            self._sleep(min(retry_after if retry_after is not None else delay, 30.0))
        raise LLMError(f"{what}: retries exhausted")  # pragma: no cover - loop always returns/raises


_RETRYABLE = {408, 409, 429, 500, 502, 503, 504, 529}


def _retry_after(v: str | None) -> float | None:
    try:
        return max(0.0, float(v)) if v else None
    except ValueError:
        return None


class NoneProvider(LLMProvider):
    name = "none"

    @property
    def enabled(self) -> bool:
        return False

    def complete_json(self, system: str, prompt: str, max_tokens: int | None = None,
                      schema: dict | None = None) -> LLMResult:
        raise LLMError("LLM provider is 'none'")


class AnthropicProvider(LLMProvider):
    """Messages API. With a `schema`, the model must answer by calling one tool whose input is
    that schema (structured output: no prose, no code fences). The static system prompt and tool
    definition are marked for prompt caching (applies once they exceed the model's minimum size)."""

    name = "anthropic"
    supports_batch = True
    TOOL = "emit_result"

    @property
    def _base(self) -> str:
        return (self.settings.llm_base_url or "https://api.anthropic.com").rstrip("/")

    @property
    def _headers(self) -> dict:
        return {"x-api-key": self.settings.llm_api_key, "anthropic-version": "2023-06-01",
                "content-type": "application/json"}

    def params(self, system: str, prompt: str, max_tokens: int | None = None, schema: dict | None = None) -> dict:
        cache = {"cache_control": {"type": "ephemeral"}} if self.settings.llm_prompt_caching else {}
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.settings.llm_max_output_tokens,
            "system": [{"type": "text", "text": system, **cache}],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        if schema is not None:
            body["tools"] = [{"name": self.TOOL, "description": "Return the result. Call exactly once.",
                              "input_schema": schema, **cache}]
            body["tool_choice"] = {"type": "tool", "name": self.TOOL}
        return body

    def parse_message(self, payload: dict) -> LLMResult:
        usage = payload.get("usage") or {}
        itok = int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) \
            + int(usage.get("cache_creation_input_tokens") or 0)
        otok = int(usage.get("output_tokens") or 0)
        blocks = payload.get("content") or []
        for b in blocks:
            if b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
                return LLMResult(b["input"], itok, otok, json.dumps(b["input"])[:2000])
        if payload.get("stop_reason") == "max_tokens":
            raise LLMError("model output truncated (max_tokens)", itok, otok)
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        try:
            return LLMResult(parse_json_object(text), itok, otok, text)
        except ValueError as e:
            raise LLMError(f"unparseable model output: {e}", itok, otok) from None

    def complete_json(self, system: str, prompt: str, max_tokens: int | None = None,
                      schema: dict | None = None) -> LLMResult:
        with self._client() as c:
            r = self._post(c, f"{self._base}/v1/messages", self.params(system, prompt, max_tokens, schema),
                           self._headers, "anthropic")
        if r.status_code != 200:
            raise LLMError(f"anthropic HTTP {r.status_code}: {r.text[:300]}")
        return self.parse_message(r.json())

    # ------------------------------------------------------------ Message Batches API
    def submit_batch(self, requests: list[tuple[str, dict]]) -> str:
        """requests: [(custom_id, params)] -> batch id."""
        body = {"requests": [{"custom_id": cid, "params": p} for cid, p in requests]}
        with self._client() as c:
            r = self._post(c, f"{self._base}/v1/messages/batches", body, self._headers, "anthropic batch")
        if r.status_code != 200:
            raise LLMError(f"anthropic batch HTTP {r.status_code}: {r.text[:300]}")
        return r.json()["id"]

    def get_batch(self, batch_id: str) -> dict:
        with self._client() as c:
            try:
                r = c.get(f"{self._base}/v1/messages/batches/{batch_id}", headers=self._headers)
            except httpx.HTTPError as e:
                raise LLMError(f"anthropic batch transport error: {e}") from None
        if r.status_code != 200:
            raise LLMError(f"anthropic batch HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    def batch_results(self, batch: dict) -> dict[str, LLMResult | LLMError]:
        url = batch.get("results_url")
        if not url:
            raise LLMError("batch has no results_url")
        with self._client() as c:
            try:
                r = c.get(url, headers=self._headers)
            except httpx.HTTPError as e:
                raise LLMError(f"anthropic batch results transport error: {e}") from None
        if r.status_code != 200:
            raise LLMError(f"anthropic batch results HTTP {r.status_code}: {r.text[:300]}")
        out: dict[str, LLMResult | LLMError] = {}
        for line in r.text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            res = row.get("result") or {}
            if res.get("type") == "succeeded":
                try:
                    out[row["custom_id"]] = self.parse_message(res.get("message") or {})
                except LLMError as e:
                    out[row["custom_id"]] = e
            else:
                out[row["custom_id"]] = LLMError(f"batch item {res.get('type')}: {json.dumps(res.get('error'))[:200]}")
        return out

    def cancel_batch(self, batch_id: str) -> None:
        with self._client() as c:
            try:
                c.post(f"{self._base}/v1/messages/batches/{batch_id}/cancel", headers=self._headers)
            except httpx.HTTPError:
                pass


class OpenAIProvider(LLMProvider):
    name = "openai"

    def complete_json(self, system: str, prompt: str, max_tokens: int | None = None,
                      schema: dict | None = None) -> LLMResult:
        base = (self.settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens or self.settings.llm_max_output_tokens,
            "temperature": 0,
        }
        headers = {"authorization": f"Bearer {self.settings.llm_api_key}"}
        with self._client() as c:
            r = self._post(c, f"{base}/chat/completions", body, headers, "openai")
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
