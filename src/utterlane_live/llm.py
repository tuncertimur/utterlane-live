"""Brains: where the assistant's replies come from.

All brains receive the same input: system instructions plus the conversation as messages
whose user turns are prefixed with the speaker label (``[Speaker: Tuncer] …``). They stream
text back. A reply that is empty or starts with a silence marker (``<silent>`` /
``<sessiz>``) means "stay quiet".

- ``OpenAIResponsesBrain``: OpenAI Responses API, ``store=false``, spend-capped.
- ``ChatCompletionsBrain``: any OpenAI-compatible ``/v1/chat/completions`` server (Ollama,
  LM Studio, OpenRouter, vLLM, or an agent gateway such as OpenClaw / Hermes Agent).
- ``WebhookBrain``: POSTs the turn to your endpoint; JSON or Server-Sent Events back.
- ``MockBrain``: deterministic local replies for tests and demos.

Only conversation text is sent — never audio, voice prints or file paths.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from utterlane_live.config import BrainConfig
from utterlane_live.i18n import t

# USD per 1M tokens: input, cached input, output (developers.openai.com/api/docs/pricing,
# checked 2026-10-14). Unknown models are not priced (spend shows 0).
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6-sol": (2.00, 0.20, 10.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
}

SILENT_TOKENS = ("<silent>", "<sessiz>")


def silent_token(language: str) -> str:
    return "<sessiz>" if language == "tr" else "<silent>"


@dataclass(slots=True)
class Message:
    role: str  # "user" | "assistant" | "developer"
    content: str


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class LlmError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


class LlmClient(Protocol):
    model_id: str
    mode: str

    def stream(
        self, instructions: str, messages: list[Message], usage_out: Usage
    ) -> AsyncIterator[str]: ...


class SpendLedger:
    """Cumulative spend persisted as JSON in the data directory."""

    def __init__(self, path: Path, cap_usd: float) -> None:
        self.path = path
        self.cap_usd = cap_usd
        self.spent_usd = 0.0
        self.calls = 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.spent_usd = float(data.get("spent_usd", 0.0))
            self.calls = int(data.get("calls", 0))
        except (OSError, ValueError):
            pass

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def can_spend(self, estimate_usd: float) -> bool:
        return self.spent_usd + estimate_usd <= self.cap_usd

    def record(self, cost_usd: float) -> None:
        self.spent_usd += cost_usd
        self.calls += 1
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "spent_usd": round(self.spent_usd, 8),
                        "calls": self.calls,
                        "updated_at": time.time(),
                    }
                ),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError:
            pass

    def public(self) -> dict[str, float | int]:
        return {"spent_usd": round(self.spent_usd, 6), "cap_usd": self.cap_usd, "calls": self.calls}


def cost_of(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    if model not in PRICES:
        return 0.0
    price_in, price_cached, price_out = PRICES[model]
    fresh = max(0, input_tokens - cached_tokens)
    return (fresh * price_in + cached_tokens * price_cached + output_tokens * price_out) / 1_000_000


def estimate_tokens(text: str) -> int:
    # Conservative for Turkish: ~3 characters per token.
    return len(text) // 3 + 16


def _error_code(exc: Exception) -> str:
    return {
        "AuthenticationError": "auth_failed",
        "PermissionDeniedError": "auth_failed",
        "RateLimitError": "rate_limited",
        "APITimeoutError": "timeout",
        "APIConnectionError": "connection_failed",
        "ConnectError": "connection_failed",
        "ConnectTimeout": "timeout",
        "ReadTimeout": "timeout",
        "BadRequestError": "request_rejected",
        "NotFoundError": "model_not_found",
    }.get(type(exc).__name__, "provider_error")


def _provider_error(lang: str, exc: Exception) -> LlmError:
    code = _error_code(exc)
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    detail = f" (HTTP {status})" if status else ""
    return LlmError(code, t(lang, "provider_failed", code=code, detail=detail))


@dataclass
class OpenAIResponsesBrain:
    """OpenAI Responses API with a persisted spend cap."""

    api_key: str
    ledger: SpendLedger
    model_id: str = "gpt-6-luna"
    reasoning_effort: str | None = "none"
    max_output_tokens: int = 400
    timeout_s: float = 30.0
    lang: str = "en"
    base_url: str | None = None
    user: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    mode: str = "live"
    _client: Any = field(default=None, init=False, repr=False)

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.api_key or "not-needed",
                base_url=self.base_url,
                max_retries=1,
                timeout=self.timeout_s,
                default_headers=self.extra_headers or None,
            )
        return self._client

    async def warmup(self) -> None:
        """Open the HTTPS connection early with a free metadata request (no tokens)."""

        with contextlib.suppress(Exception):
            await self._get_client().models.retrieve(self.model_id)

    async def stream(
        self, instructions: str, messages: list[Message], usage_out: Usage
    ) -> AsyncIterator[str]:
        prompt_chars = len(instructions) + sum(len(m.content) for m in messages)
        estimate = cost_of(
            self.model_id, estimate_tokens("x" * prompt_chars), 0, self.max_output_tokens
        )
        if not self.ledger.can_spend(estimate):
            raise LlmError(
                "budget_exhausted",
                t(
                    self.lang,
                    "budget_exhausted",
                    spent=self.ledger.spent_usd,
                    cap=self.ledger.cap_usd,
                ),
            )
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "instructions": instructions,
            "input": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "store": False,
            "max_output_tokens": self.max_output_tokens,
        }
        if self.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        if self.user:
            kwargs["user"] = self.user
        try:
            stream = await self._get_client().responses.create(**kwargs)
            async for event in stream:
                kind = getattr(event, "type", "")
                if kind == "response.output_text.delta":
                    yield event.delta
                elif kind == "response.completed":
                    usage = event.response.usage
                    if usage is not None:
                        details = getattr(usage, "input_tokens_details", None)
                        cached = getattr(details, "cached_tokens", 0) or 0
                        usage_out.input_tokens = usage.input_tokens
                        usage_out.cached_tokens = cached
                        usage_out.output_tokens = usage.output_tokens
                        usage_out.cost_usd = cost_of(
                            self.model_id, usage.input_tokens, cached, usage.output_tokens
                        )
                        self.ledger.record(usage_out.cost_usd)
                elif kind in {"response.failed", "error"}:
                    raise LlmError(
                        "provider_failed", t(self.lang, "provider_failed", code="failed", detail="")
                    )
        except (LlmError, asyncio.CancelledError):
            raise
        except Exception as exc:  # map SDK errors to stable, non-secret codes
            raise _provider_error(self.lang, exc) from None


# Backwards-compatible name.
OpenAILlm = OpenAIResponsesBrain


@dataclass
class ChatCompletionsBrain:
    """Any OpenAI-compatible chat-completions server (streaming)."""

    base_url: str
    model_id: str
    api_key: str | None = None
    session: str = "utterlane-room"
    extra_headers: dict[str, str] = field(default_factory=dict)
    max_output_tokens: int = 400
    timeout_s: float = 60.0
    ledger: SpendLedger | None = None
    lang: str = "en"
    mode: str = "live"
    _client: Any = field(default=None, init=False, repr=False)

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key or "not-needed",
                max_retries=1,
                timeout=self.timeout_s,
                default_headers=self.extra_headers or None,
            )
        return self._client

    async def warmup(self) -> None:
        return None

    async def stream(
        self, instructions: str, messages: list[Message], usage_out: Usage
    ) -> AsyncIterator[str]:
        chat = [{"role": "system", "content": instructions}]
        for message in messages:
            role = "system" if message.role == "developer" else message.role
            chat.append({"role": role, "content": message.content})
        kwargs: dict[str, Any] = {
            "model": self.model_id or "default",
            "messages": chat,
            "stream": True,
            "max_tokens": self.max_output_tokens,
            "user": self.session,
        }
        try:
            try:
                stream = await self._get_client().chat.completions.create(
                    **kwargs, stream_options={"include_usage": True}
                )
            except Exception as exc:
                if _error_code(exc) != "request_rejected":
                    raise
                stream = await self._get_client().chat.completions.create(**kwargs)
            async for chunk in stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    usage_out.input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    usage_out.output_tokens = getattr(usage, "completion_tokens", 0) or 0
                    usage_out.cost_usd = cost_of(
                        self.model_id, usage_out.input_tokens, 0, usage_out.output_tokens
                    )
                    if self.ledger is not None and usage_out.cost_usd:
                        self.ledger.record(usage_out.cost_usd)
                for choice in getattr(chunk, "choices", None) or []:
                    delta = getattr(choice, "delta", None)
                    text = getattr(delta, "content", None) if delta is not None else None
                    if text:
                        yield text
        except (LlmError, asyncio.CancelledError):
            raise
        except Exception as exc:
            raise _provider_error(self.lang, exc) from None


@dataclass
class WebhookBrain:
    """POST the conversation to your own endpoint and speak its answer.

    Request JSON: ``{"session", "language", "instructions", "messages": [{role, content}],
    "latest": "<last user message>"}``. Response: JSON ``{"reply": "..."}`` (or ``{"text"}``),
    plain text, or ``text/event-stream`` with ``data: {"delta": "..."}`` lines.
    An empty reply means "stay silent".
    """

    url: str
    api_key: str | None = None
    session: str = "utterlane-room"
    extra_headers: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 60.0
    lang: str = "en"
    model_id: str = "webhook"
    mode: str = "live"

    async def warmup(self) -> None:
        return None

    async def stream(
        self, instructions: str, messages: list[Message], usage_out: Usage
    ) -> AsyncIterator[str]:
        import httpx

        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        latest = next((m.content for m in reversed(messages) if m.role == "user"), "")
        payload = {
            "session": self.session,
            "language": self.lang,
            "instructions": instructions,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "latest": latest,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                async with client.stream(
                    "POST", self.url, json=payload, headers=headers
                ) as response:
                    response.raise_for_status()
                    kind = response.headers.get("content-type", "")
                    if "text/event-stream" in kind:
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                item = json.loads(data)
                            except ValueError:
                                yield data
                                continue
                            text = item.get("delta") or item.get("text") or ""
                            if text:
                                yield str(text)
                        return
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    if "json" in kind:
                        item = json.loads(body or "{}")
                        body = str(item.get("reply") or item.get("text") or "")
                    if body.strip():
                        yield body
        except (LlmError, asyncio.CancelledError):
            raise
        except Exception as exc:
            raise _provider_error(self.lang, exc) from None


class MockBrain:
    """Deterministic local stand-in used by tests and the no-brain demo mode."""

    model_id = "mock"
    mode = "mock"

    def __init__(
        self,
        responder: Callable[[str, list[Message]], str] | None = None,
        delay_s: float = 0.0,
        lang: str = "tr",
    ) -> None:
        self.responder = responder or self._default
        self.delay_s = delay_s
        self.lang = lang
        self.calls: list[tuple[str, list[Message]]] = []

    def _default(self, instructions: str, messages: list[Message]) -> str:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        speaker = "sen" if self.lang == "tr" else "there"
        if last.startswith("[") and ":" in last:
            speaker = last.split("]", 1)[0].split(":", 1)[1].strip()
        if (
            any(token in instructions for token in SILENT_TOKENS)
            and "lena" not in last.lower()
            and "?" not in last
        ):
            return SILENT_TOKENS[1] if self.lang == "tr" else SILENT_TOKENS[0]
        return t(self.lang, "mock_reply", speaker=speaker)

    async def warmup(self) -> None:
        return None

    async def stream(
        self, instructions: str, messages: list[Message], usage_out: Usage
    ) -> AsyncIterator[str]:
        self.calls.append((instructions, list(messages)))
        text = self.responder(instructions, messages)
        for word in text.split(" "):
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield word + " "
        usage_out.input_tokens = estimate_tokens(
            instructions + "".join(m.content for m in messages)
        )
        usage_out.output_tokens = estimate_tokens(text)


# Backwards-compatible name.
MockLlm = MockBrain


class NoBrain:
    """Listen-only: no generated replies (deterministic prompts such as consent still speak)."""

    model_id = "none"
    mode = "off"

    async def warmup(self) -> None:
        return None

    async def stream(
        self, instructions: str, messages: list[Message], usage_out: Usage
    ) -> AsyncIterator[str]:
        return
        yield ""  # pragma: no cover - makes this an async generator


def display_url(url: str | None) -> str:
    """A URL for status displays: no user:password, query string or fragment (may hold keys)."""

    if not url:
        return ""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parts.port:
        host += f":{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def build_brain(brain: BrainConfig, ledger: SpendLedger, lang: str) -> tuple[Any, str]:
    """Create the configured brain; returns (brain, human-readable description)."""

    if brain.api == "responses":
        official = brain.kind == "openai"
        if official and not brain.api_key:
            return NoBrain(), "none (OPENAI_API_KEY not set)"
        if not official and not brain.url:
            raise ValueError(f"UTTERLANE_BRAIN={brain.kind} needs UTTERLANE_BRAIN_URL")
        return (
            OpenAIResponsesBrain(
                api_key=brain.api_key or "",
                ledger=ledger,
                model_id=brain.model or "gpt-6-luna",
                reasoning_effort=brain.reasoning_effort if official else None,
                max_output_tokens=brain.max_output_tokens,
                timeout_s=brain.timeout_s,
                lang=lang,
                base_url=None if official else brain.url,
                user=None if official else brain.session,
                extra_headers=brain.extra_headers,
            ),
            f"{brain.kind} {brain.model}" + ("" if official else f" @ {display_url(brain.url)}"),
        )
    if brain.api == "chat":
        if not brain.url:
            raise ValueError(
                f"UTTERLANE_BRAIN={brain.kind} needs UTTERLANE_BRAIN_URL (e.g. http://127.0.0.1:11434/v1)"
            )
        if brain.kind in ("ollama", "lmstudio", "chat") and not brain.model:
            raise ValueError(
                f"UTTERLANE_BRAIN={brain.kind} needs UTTERLANE_BRAIN_MODEL (e.g. qwen3:8b)"
            )
        return (
            ChatCompletionsBrain(
                base_url=brain.url,
                model_id=brain.model,
                api_key=brain.api_key,
                session=brain.session,
                extra_headers=brain.extra_headers,
                max_output_tokens=brain.max_output_tokens,
                timeout_s=brain.timeout_s,
                ledger=ledger,
                lang=lang,
            ),
            f"{brain.kind} {brain.model or 'default'} @ {display_url(brain.url)}",
        )
    if brain.api == "webhook":
        if not brain.url:
            raise ValueError("UTTERLANE_BRAIN=webhook needs UTTERLANE_BRAIN_URL")
        return (
            WebhookBrain(
                url=brain.url,
                api_key=brain.api_key,
                session=brain.session,
                extra_headers=brain.extra_headers,
                timeout_s=brain.timeout_s,
                lang=lang,
            ),
            f"webhook {display_url(brain.url)}",
        )
    if brain.api == "mock":
        return MockBrain(lang=lang), "mock (local placeholder replies)"
    return NoBrain(), "none (listen only)"
