"""Brains against a local fake OpenAI-compatible server (chat, responses, webhook, presets)."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from utterlane_live.config import LiveConfig
from utterlane_live.llm import (
    ChatCompletionsBrain,
    LlmError,
    Message,
    NoBrain,
    OpenAIResponsesBrain,
    SpendLedger,
    Usage,
    WebhookBrain,
    build_brain,
    display_url,
)

SEEN: list[dict[str, Any]] = []


def fake_app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> StreamingResponse:
        body = await request.json()
        SEEN.append({"path": "chat", "headers": dict(request.headers), "body": body})

        def events() -> Iterator[str]:
            for word in ["Merhaba ", "Tuncer", "."]:
                chunk = {
                    "id": "c1",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": body["model"],
                    "choices": [{"index": 0, "delta": {"content": word}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
            yield ": keepalive\n\n"
            usage = {
                "id": "c1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": body["model"],
                "choices": [],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            }
            yield f"data: {json.dumps(usage)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/v1/responses")
    async def responses(request: Request) -> StreamingResponse:
        body = await request.json()
        SEEN.append({"path": "responses", "headers": dict(request.headers), "body": body})

        def events() -> Iterator[str]:
            yield 'event: response.created\ndata: {"type":"response.created","sequence_number":0,"response":{"id":"r1","object":"response","created_at":0,"model":"openclaw/default","output":[],"status":"in_progress","parallel_tool_calls":false,"tool_choice":"auto","tools":[]}}\n\n'
            for i, word in enumerate(["Hello ", "there."]):
                delta = {
                    "type": "response.output_text.delta",
                    "sequence_number": i + 1,
                    "item_id": "m1",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": word,
                    "logprobs": [],
                }
                yield f"event: response.output_text.delta\ndata: {json.dumps(delta)}\n\n"
            done = {
                "type": "response.completed",
                "sequence_number": 9,
                "response": {
                    "id": "r1",
                    "object": "response",
                    "created_at": 0,
                    "model": "openclaw/default",
                    "output": [],
                    "status": "completed",
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "total_tokens": 12,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 0},
                    },
                },
            }
            yield f"event: response.completed\ndata: {json.dumps(done)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/hook/json")
    async def hook_json(request: Request) -> JSONResponse:
        body = await request.json()
        SEEN.append({"path": "hook", "headers": dict(request.headers), "body": body})
        return JSONResponse({"reply": f"Got: {body['latest']}"})

    @app.post("/hook/sse")
    async def hook_sse() -> StreamingResponse:
        def events() -> Iterator[str]:
            yield 'data: {"delta": "Bir "}\n\n'
            yield 'data: {"delta": "iki."}\n\n'
            yield "data: [DONE]\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/hook/silent")
    async def hook_silent() -> PlainTextResponse:
        return PlainTextResponse("")

    @app.post("/hook/down")
    async def hook_down() -> PlainTextResponse:
        return PlainTextResponse("nope", status_code=503)

    return app


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(fake_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def collect(brain: Any, messages: list[Message] | None = None) -> tuple[str, Usage]:
    usage = Usage()

    async def run() -> str:
        parts = []
        async for delta in brain.stream(
            "Be brief.", messages or [Message("user", "[Speaker: Tuncer] hi")], usage
        ):
            parts.append(delta)
        return "".join(parts)

    return asyncio.run(run()), usage


def test_chat_completions_stream_with_session_headers(server_url: str, tmp_path: Path) -> None:
    SEEN.clear()
    brain = ChatCompletionsBrain(
        base_url=f"{server_url}/v1",
        model_id="hermes-agent",
        api_key="k-123",
        session="room-1",
        extra_headers={"X-Hermes-Session-Id": "room-1"},
        ledger=SpendLedger(tmp_path / "u.json", 1.0),
    )
    text, usage = collect(
        brain, [Message("developer", "Room: Tuncer."), Message("user", "[Speaker: Tuncer] hi")]
    )
    assert text == "Merhaba Tuncer."
    assert usage.input_tokens == 12 and usage.output_tokens == 3 and usage.cost_usd == 0.0
    request = SEEN[-1]
    assert request["headers"]["authorization"] == "Bearer k-123"
    assert request["headers"]["x-hermes-session-id"] == "room-1"
    body = request["body"]
    assert body["model"] == "hermes-agent" and body["user"] == "room-1" and body["stream"] is True
    assert [m["role"] for m in body["messages"]] == ["system", "system", "user"]


def test_responses_api_against_custom_base_url(server_url: str, tmp_path: Path) -> None:
    SEEN.clear()
    brain = OpenAIResponsesBrain(
        api_key="gw-token",
        ledger=SpendLedger(tmp_path / "u.json", 1.0),
        model_id="openclaw/default",
        reasoning_effort=None,
        base_url=f"{server_url}/v1",
        user="utterlane-room",
        extra_headers={"x-openclaw-session-key": "utterlane-room"},
    )
    text, usage = collect(brain)
    assert text == "Hello there."
    request = SEEN[-1]
    assert request["path"] == "responses"
    assert request["headers"]["authorization"] == "Bearer gw-token"
    assert request["headers"]["x-openclaw-session-key"] == "utterlane-room"
    assert request["body"]["user"] == "utterlane-room"
    assert "reasoning" not in request["body"]
    assert usage.output_tokens == 2


def test_webhook_json_sse_silence_and_errors(server_url: str) -> None:
    SEEN.clear()
    text, _ = collect(WebhookBrain(url=f"{server_url}/hook/json", api_key="w", session="s1"))
    assert text == "Got: [Speaker: Tuncer] hi"
    assert (
        SEEN[-1]["body"]["session"] == "s1" and SEEN[-1]["headers"]["authorization"] == "Bearer w"
    )
    text, _ = collect(WebhookBrain(url=f"{server_url}/hook/sse"))
    assert text == "Bir iki."
    text, _ = collect(WebhookBrain(url=f"{server_url}/hook/silent"))
    assert text == ""
    with pytest.raises(LlmError) as error:
        collect(WebhookBrain(url=f"{server_url}/hook/down"))
    assert error.value.code == "provider_error"


def test_unreachable_server_maps_to_connection_error(tmp_path: Path) -> None:
    brain = ChatCompletionsBrain(base_url="http://127.0.0.1:9/v1", model_id="x", timeout_s=2)
    with pytest.raises(LlmError) as error:
        collect(brain)
    assert error.value.code in ("connection_failed", "timeout")


@pytest.mark.parametrize(
    ("env", "kind", "api", "url", "model", "history", "speakers"),
    [
        ({"OPENAI_API_KEY": "sk-x"}, "openai", "responses", None, "gpt-6-luna", "full", "all"),
        (
            {"UTTERLANE_BRAIN": "openclaw"},
            "openclaw",
            "responses",
            "http://127.0.0.1:18789/v1",
            "openclaw/default",
            "latest",
            "named",
        ),
        (
            {"UTTERLANE_BRAIN": "hermes"},
            "hermes",
            "chat",
            "http://127.0.0.1:8642/v1",
            "hermes-agent",
            "latest",
            "named",
        ),
        (
            {"UTTERLANE_BRAIN": "ollama", "UTTERLANE_BRAIN_MODEL": "qwen3:8b"},
            "ollama",
            "chat",
            "http://127.0.0.1:11434/v1",
            "qwen3:8b",
            "full",
            "all",
        ),
        (
            {
                "UTTERLANE_BRAIN": "chat",
                "UTTERLANE_BRAIN_URL": "http://x/v1",
                "UTTERLANE_BRAIN_MODEL": "m",
            },
            "chat",
            "chat",
            "http://x/v1",
            "m",
            "full",
            "all",
        ),
        (
            {"UTTERLANE_BRAIN": "webhook", "UTTERLANE_BRAIN_URL": "http://x/hook"},
            "webhook",
            "webhook",
            "http://x/hook",
            "",
            "latest",
            "all",
        ),
        ({}, "none", "none", None, "", "full", "all"),
    ],
)
def test_brain_presets(
    env: dict[str, str],
    kind: str,
    api: str,
    url: str | None,
    model: str,
    history: str,
    speakers: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("UTTERLANE_") or key == "OPENAI_API_KEY":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)  # no stray .env
    monkeypatch.setenv("UTTERLANE_DATA_DIR", str(tmp_path / "data"))
    cfg = LiveConfig.from_env(env)
    brain = cfg.brain
    assert (brain.kind, brain.api, brain.url, brain.model, brain.history, brain.speakers) == (
        kind,
        api,
        url,
        model,
        history,
        speakers,
    )
    if kind == "hermes":
        assert brain.extra_headers["X-Hermes-Session-Id"] == "utterlane-room"
    if kind == "openclaw":
        assert brain.extra_headers["x-openclaw-session-key"] == "utterlane-room"


def test_build_brain_without_key_is_listen_only(tmp_path: Path) -> None:
    cfg = LiveConfig.from_env({"UTTERLANE_DATA_DIR": str(tmp_path)})
    cfg.brain.kind = "openai"
    cfg.brain.api = "responses"
    cfg.brain.api_key = None
    brain, description = build_brain(cfg.brain, SpendLedger(tmp_path / "u.json", 1.0), "en")
    assert isinstance(brain, NoBrain) and "OPENAI_API_KEY" in description
    cfg.brain.kind, cfg.brain.api, cfg.brain.url, cfg.brain.model = (
        "ollama",
        "chat",
        "http://x/v1",
        "",
    )
    with pytest.raises(ValueError, match="UTTERLANE_BRAIN_MODEL"):
        build_brain(cfg.brain, SpendLedger(tmp_path / "u.json", 1.0), "en")


def test_status_descriptions_never_show_credentials_in_urls(tmp_path: Path) -> None:
    assert display_url("https://user:pw@example.com:8443/x?token=abc#f") == (
        "https://example.com:8443/x"
    )
    assert display_url("http://[::1]:8000/v1") == "http://[::1]:8000/v1"
    cfg = LiveConfig.from_env({"UTTERLANE_DATA_DIR": str(tmp_path)})
    cfg.brain.kind, cfg.brain.api = "webhook", "webhook"
    cfg.brain.url = "https://hook.example/x?key=sekret"
    _, description = build_brain(cfg.brain, SpendLedger(tmp_path / "u.json", 1.0), "en")
    assert "sekret" not in description and "hook.example/x" in description
