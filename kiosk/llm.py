"""LLM client: OpenRouter primary, local Qwen fallback, both via the Responses API.

Calls are stateless (store=False); the caller keeps the history and sends it in
`input`. The primary is always tried first, so it recovers on its own after an
outage. Exceptions are logged by type only, never by message, so nothing
key-like can reach the logs.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import openai

from kiosk.config import Config, Secrets

log = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PRIMARY_NAME = "openrouter"
FALLBACK_NAME = "fallback"


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class LlmResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Raw Responses output items; append to `input` on the next turn.
    output_items: list[dict] = field(default_factory=list)
    model: str = ""


class LlmUnavailable(Exception):
    """Both the primary and the fallback backend failed."""


@dataclass(frozen=True)
class Backend:
    name: str
    model: str
    client: openai.AsyncOpenAI


def _as_dict(item: Any) -> dict:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    return dict(vars(item))


def _parse_arguments(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"_invalid": raw if isinstance(raw, str) else repr(raw)}
    if not isinstance(parsed, dict):
        return {"_invalid": raw}
    return parsed


def _parse(response: Any, model: str) -> LlmResponse:
    items = [_as_dict(i) for i in (getattr(response, "output", None) or [])]
    texts: list[str] = []
    calls: list[ToolCall] = []
    for item in items:
        kind = item.get("type")
        if kind == "function_call":
            calls.append(
                ToolCall(
                    call_id=item.get("call_id") or item.get("id") or "",
                    name=item.get("name", ""),
                    arguments=_parse_arguments(item.get("arguments")),
                )
            )
        elif kind == "message":
            for part in item.get("content") or []:
                part = _as_dict(part)
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
    return LlmResponse(text="".join(texts), tool_calls=calls, output_items=items, model=model)


class LlmClient:
    def __init__(self, primary: Backend, fallback: Backend, timeout_s: float):
        self._primary = primary
        self._fallback = fallback
        self._timeout_s = timeout_s
        self._active = primary.name

    @property
    def active(self) -> str:
        """Name of the last backend that succeeded."""
        return self._active

    async def _call(self, backend: Backend, kwargs: dict) -> LlmResponse:
        response = await backend.client.responses.create(
            model=backend.model, store=False, timeout=self._timeout_s, **kwargs
        )
        return _parse(response, backend.model)

    async def probe(self, which: str, *, instructions: str, input: str) -> LlmResponse:
        """One call to a single backend ("openrouter" or "fallback"): no retry, no
        failover, and `active` is left alone. For the preflight checklist. Raises on failure."""
        backend = {PRIMARY_NAME: self._primary, FALLBACK_NAME: self._fallback}[which]
        return await self._call(backend, {"instructions": instructions, "input": input})

    async def respond(
        self,
        *,
        instructions: str,
        input: list[dict] | str,
        tools: list[dict] | None = None,
        json_schema: dict | None = None,
        tool_choice: str = "auto",
    ) -> LlmResponse:
        kwargs: dict[str, Any] = {"instructions": instructions, "input": input}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if json_schema is not None:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "out",
                    "schema": json_schema,
                    "strict": True,
                }
            }

        # Primary twice, then fallback once.
        attempts = [self._primary, self._primary, self._fallback]
        for backend in attempts:
            try:
                result = await self._call(backend, kwargs)
            except Exception as exc:  # network, timeout, HTTP status, bad payload
                log.warning("LLM backend %s failed: %s", backend.name, type(exc).__name__)
                continue
            self._active = backend.name
            return result
        raise LlmUnavailable("primary and fallback LLM backends both failed")


def make_llm_client(cfg: Config, secrets: Secrets) -> LlmClient:
    # max_retries=0: LlmClient owns the retry policy.
    primary = Backend(
        PRIMARY_NAME,
        cfg.models.llm,
        openai.AsyncOpenAI(
            api_key=secrets.openrouter_api_key, base_url=OPENROUTER_BASE_URL, max_retries=0
        ),
    )
    fallback = Backend(
        FALLBACK_NAME,
        cfg.models.fallback_llm,
        openai.AsyncOpenAI(
            api_key=secrets.fallback_llm_key, base_url=secrets.fallback_llm_url, max_retries=0
        ),
    )
    return LlmClient(primary, fallback, cfg.timeouts.llm_call_s)
