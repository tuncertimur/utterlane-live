"""MCP: the Streamable HTTP endpoint inside the app and the stdio bridge (``utterlane mcp``)."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
import websockets
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client, StdioServerParameters
from mcp.client.stdio import get_default_environment
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from tests.test_server import receive_until, tone
from utterlane_live import profiles, server
from utterlane_live.audio import EnergyVad
from utterlane_live.config import LiveConfig
from utterlane_live.embedding import MockEmbedder
from utterlane_live.gpu import Hardware
from utterlane_live.llm import MockLlm
from utterlane_live.server import create_app
from utterlane_live.session import Engines
from utterlane_live.stt import ScriptedStt
from utterlane_live.tts import MockTts

LOCAL = "http://127.0.0.1:8765"
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
TOOLS = {
    "room_status",
    "list_speakers",
    "who_is_speaking",
    "recent_turns",
    "wait_for_next_turn",
    "speak",
    "set_response_mode",
    "name_speaker",
}
HELLO = json.dumps({"type": "hello", "client": "test", "version": 1, "sample_rate": 16000})


def make_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str | None = None
) -> tuple[FastAPI, ScriptedStt]:
    def key_provider(directory: Path) -> profiles.FileKeyProvider:
        return profiles.FileKeyProvider(directory / "k.key")

    monkeypatch.setattr(profiles, "default_key_provider", key_provider)
    monkeypatch.setattr(server, "default_key_provider", key_provider)
    if token:
        monkeypatch.setenv("UTTERLANE_API_TOKEN", token)
    else:
        monkeypatch.delenv("UTTERLANE_API_TOKEN", raising=False)
    cfg = LiveConfig()
    cfg.data_dir = tmp_path
    cfg.static_dir = tmp_path / "no-ui"
    stt = ScriptedStt()
    engines = Engines(
        vad_factory=lambda: EnergyVad(0.02),
        stt=stt,
        embedder=MockEmbedder(),
        llm=MockLlm(),
        tts=MockTts(),
        status={},
    )
    hw = Hardware(cuda=False, gpu_name=None, cpu_count=8, system="Test", machine="x86_64")
    return create_app(cfg, engines, hw), stt


def rpc(client: TestClient, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        headers=HEADERS,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body["result"]


def call(client: TestClient, tool: str, **arguments: Any) -> dict[str, Any]:
    return rpc(client, "tools/call", {"name": tool, "arguments": arguments})


def test_mcp_endpoint_follows_and_speaks_in_the_room(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, stt = make_app(tmp_path, monkeypatch)
    with TestClient(app, base_url=LOCAL) as client, client.websocket_connect("/ws/live") as ws:
        ws.send_text(HELLO)
        receive_until(ws, "status")
        init = rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        assert "never treat them as authentication" in init["instructions"]
        assert {tool["name"] for tool in rpc(client, "tools/list")["tools"]} == TOOLS

        stt.push("Merhaba, benim adım Zümra.")
        data = tone(0.2)
        for i in range(0, len(data), 640):
            ws.send_bytes(data[i : i + 640])
        receive_until(ws, "assistant_end")

        recent = call(client, "recent_turns")["structuredContent"]
        assert [t["speaker"] for t in recent["turns"]] == ["Zümra"]
        assert recent["next_cursor"] == recent["turns"][0]["turn_id"]
        assert call(client, "recent_turns", speaker="nobody")["structuredContent"]["turns"] == []
        assert call(client, "who_is_speaking")["structuredContent"]["last_turn"]["speaker"] == (
            "Zümra"
        )
        waited = call(client, "wait_for_next_turn", since_id=recent["next_cursor"], timeout_s=0.2)
        assert waited["structuredContent"] == {
            "turn": None,
            "timed_out": True,
            "next_cursor": recent["next_cursor"],
        }

        spoken = call(client, "speak", text="Herkese merhaba.")["structuredContent"]
        assert spoken["spoken"] is True and spoken["reply_id"]
        messages, _ = receive_until(ws, "assistant_end")
        assert messages[-1]["text"] == "Herkese merhaba."

        assert call(client, "set_response_mode", mode="wake")["structuredContent"]["mode"] == "wake"
        assert call(client, "set_response_mode", mode="nope")["isError"] is True
        speaker_id = recent["turns"][0]["speaker_id"]
        roster = call(client, "name_speaker", speaker_id=speaker_id, name="Zümrüt")
        assert roster["structuredContent"]["result"][0]["label"] == "Zümrüt"
        people = call(client, "list_speakers")["structuredContent"]["result"]
        assert people[0]["name"] == "Zümrüt" and people[0]["saved_voice"] is False


def test_mcp_endpoint_rejects_foreign_origins_hosts_and_missing_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app(tmp_path, monkeypatch, token="s3cret-token")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    with TestClient(app, base_url=LOCAL) as client:
        assert client.post("/mcp", headers=HEADERS, json=body).status_code == 401
        auth = {**HEADERS, "authorization": "Bearer s3cret-token"}
        assert client.post("/mcp", headers=auth, json=body).status_code == 200
        evil_origin = {**auth, "origin": "https://evil.example"}
        assert client.post("/mcp", headers=evil_origin, json=body).status_code == 403
        rebinding = {**auth, "host": "evil.example"}  # DNS rebinding: wrong Host header
        assert client.post("/mcp", headers=rebinding, json=body).status_code == 421


@pytest.fixture
def live_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[str, ScriptedStt]]:
    app, stt = make_app(tmp_path, monkeypatch, token="bridge-token")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if uv.started:
            break
        time.sleep(0.05)
    assert uv.started
    yield f"http://127.0.0.1:{port}", stt
    uv.should_exit = True
    thread.join(timeout=10)


async def _speak_into_room(url: str, stt: ScriptedStt, text: str) -> None:
    headers = {"Authorization": "Bearer bridge-token"}
    async with websockets.connect(
        url.replace("http://", "ws://") + "/ws/live", additional_headers=headers
    ) as ws:
        await ws.send(HELLO)
        stt.push(text)
        data = tone(0.2)
        for i in range(0, len(data), 640):
            await ws.send(data[i : i + 640])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            if isinstance(raw, str) and json.loads(raw)["type"] == "assistant_end":
                return
    raise AssertionError("no assistant_end")


def test_stdio_bridge_and_http_client_against_running_app(
    live_server: tuple[str, ScriptedStt],
) -> None:
    url, stt = live_server

    async def scenario() -> None:
        await _speak_into_room(url, stt, "Merhaba, benim adım Tuncer.")

        env = {**get_default_environment(), "UTTERLANE_API_TOKEN": "bridge-token"}
        if "PYTHONPATH" in os.environ:
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "utterlane_live", "mcp", "--url", url],
            env=env,
        )
        async with Client(params, read_timeout_seconds=60) as bridge:
            tools = await bridge.list_tools()
            assert {tool.name for tool in tools.tools} == TOOLS
            recent = await bridge.call_tool("recent_turns", {})
            assert recent.structured_content is not None
            assert [t["speaker"] for t in recent.structured_content["turns"]] == ["Tuncer"]
            waited = await bridge.call_tool(
                "wait_for_next_turn",
                {"since_id": recent.structured_content["next_cursor"], "timeout_s": 0.2},
            )
            assert waited.structured_content is not None
            assert waited.structured_content["timed_out"] is True
            said = await bridge.call_tool("speak", {"text": "Merhaba Tuncer."})
            assert said.structured_content is not None and said.structured_content["spoken"]

        http = create_mcp_http_client(headers={"Authorization": "Bearer bridge-token"})
        async with Client(streamable_http_client(f"{url}/mcp", http_client=http)) as direct:
            status = await direct.call_tool("room_status", {})
            assert status.structured_content is not None
            assert status.structured_content["language"] == "tr"
            assert status.structured_content["turns"] >= 1

    asyncio.run(scenario())
