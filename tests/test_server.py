"""HTTP + WebSocket contract tests against the real FastAPI app with mock engines."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from utterlane_live.audio import EnergyVad
from utterlane_live.config import LiveConfig
from utterlane_live.embedding import MockEmbedder
from utterlane_live.gpu import Hardware
from utterlane_live.llm import MockLlm
from utterlane_live.server import create_app
from utterlane_live.session import Engines
from utterlane_live.stt import ScriptedStt
from utterlane_live.tts import MockTts


def tone(amplitude: float, seconds: float = 1.6, silence: float = 1.0) -> bytes:
    t = np.arange(int(seconds * 16000)) / 16000
    audio = np.concatenate(
        [amplitude * np.sin(2 * np.pi * 180 * t), np.zeros(int(silence * 16000))]
    )
    return (audio * 32767).astype("<i2").tobytes()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, ScriptedStt]:
    monkeypatch.setattr(
        "utterlane_live.profiles.default_key_provider",
        lambda d: __import__(
            "utterlane_live.profiles", fromlist=["FileKeyProvider"]
        ).FileKeyProvider(d / "k.key"),
    )
    monkeypatch.setattr(
        "utterlane_live.server.default_key_provider",
        lambda d: __import__(
            "utterlane_live.profiles", fromlist=["FileKeyProvider"]
        ).FileKeyProvider(d / "k.key"),
    )
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
        status={"stt": {"id": "scripted", "status": "mock"}},
    )
    hw = Hardware(cuda=False, gpu_name=None, cpu_count=8, system="Test", machine="x86_64")
    return TestClient(create_app(cfg, engines, hw)), stt


def receive_until(ws: Any, kind: str, limit: int = 400) -> tuple[list[dict[str, Any]], list[bytes]]:
    messages: list[dict[str, Any]] = []
    audio: list[bytes] = []
    for _ in range(limit):
        raw = ws.receive()
        if raw.get("bytes") is not None:
            audio.append(raw["bytes"])
            continue
        message = json.loads(raw["text"])
        messages.append(message)
        if message["type"] == kind:
            return messages, audio
    raise AssertionError(f"{kind} not received")


def test_health_profiles_settings(setup: tuple[TestClient, ScriptedStt]) -> None:
    client, _ = setup
    with client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok" and health["models"]["stt"]["status"] == "mock"
        assert client.get("/api/profiles").json() == []
        assert client.put("/api/settings", json={"mode": "always"}).json()["mode"] == "always"
        assert client.get("/api/usage").json()["calls"] == 0
        assert client.delete("/api/profiles/nope").status_code == 404
        assert "web UI is not built" in client.get("/").text


def test_websocket_conversation_roundtrip(setup: tuple[TestClient, ScriptedStt]) -> None:
    client, stt = setup
    with client, client.websocket_connect("/ws/live") as ws:
        ws.send_text(
            json.dumps({"type": "hello", "client": "test", "version": 1, "sample_rate": 16000})
        )
        messages, _ = receive_until(ws, "status")
        assert messages[0]["type"] == "ready" and messages[0]["assistant_name"] == "Lena"
        assert messages[0]["language"] == "tr"

        stt.push("Merhaba, benim adım Zümra.")
        data = tone(0.2)
        for i in range(0, len(data), 640):
            ws.send_bytes(data[i : i + 640])
        messages, audio = receive_until(ws, "assistant_end")
        turn = next(m for m in messages if m["type"] == "turn")["turn"]
        assert turn["text"] == "Merhaba, benim adım Zümra."
        labels = [m["speaker"]["label"] for m in messages if m["type"] == "turn_update"]
        assert labels[-1] == "Zümra"
        end = messages[-1]
        assert "Zümra" in end["text"] and "ister misin" in end["text"]
        start = next(m for m in messages if m["type"] == "assistant_start")
        if not audio:
            _, audio = receive_until(ws, "audio_end")
        magic, seq, _chunk, rate = audio[0][:4], *struct.unpack("<III", audio[0][4:16])
        assert magic == b"UTA1" and seq == start["reply_seq"] and rate == 24000

        ws.send_text(json.dumps({"type": "ping", "t": 5}))
        pong, _ = receive_until(ws, "pong")
        assert pong[-1]["t"] == 5


def test_foreign_origin_rejected(setup: tuple[TestClient, ScriptedStt]) -> None:
    client, _ = setup
    with client:
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/live", headers={"origin": "https://evil.example"}
            ) as ws:
                ws.receive_text()


def test_integration_rest_api(setup: tuple[TestClient, ScriptedStt]) -> None:
    client, stt = setup
    with client, client.websocket_connect("/ws/live") as ws:
        ws.send_text(
            json.dumps({"type": "hello", "client": "test", "version": 1, "sample_rate": 16000})
        )
        receive_until(ws, "status")
        assert client.get("/api/turns").json() == []
        stt.push("Merhaba, benim adım Zümra.")
        data = tone(0.2)
        for i in range(0, len(data), 640):
            ws.send_bytes(data[i : i + 640])
        receive_until(ws, "assistant_end")

        turns = client.get("/api/turns").json()
        assert [t["speaker"]["label"] for t in turns] == ["Zümra"]
        assert client.get("/api/speakers").json()[0]["name"] == "Zümra"
        state = client.get("/api/state").json()
        assert state["turns"] == 1 and state["language"] == "tr"

        waited = client.get(
            "/api/turns/wait", params={"after": turns[0]["id"], "timeout": 0.2}
        ).json()
        assert waited == {"turn": None, "timed_out": True}
        waited = client.get("/api/turns/wait", params={"timeout": 0.2}).json()
        assert waited["timed_out"] is True

        said = client.post("/api/say", json={"text": "Herkese merhaba."}).json()
        assert said["reply_id"]
        messages, _ = receive_until(ws, "assistant_end")
        assert messages[-1]["text"] == "Herkese merhaba."

        assert client.post("/api/mode", json={"mode": "wake"}).json()["mode"] == "wake"
        assert client.post("/api/mode", json={"mode": "nope"}).status_code == 400
        roster = client.post(
            f"/api/speakers/{turns[0]['speaker']['id']}/name", json={"name": "Zümrüt"}
        ).json()
        assert roster[0]["label"] == "Zümrüt"


def test_cross_site_requests_are_rejected(setup: tuple[TestClient, ScriptedStt]) -> None:
    client, _ = setup
    with client:
        evil = {"origin": "https://evil.example"}
        assert client.post("/api/say", json={"text": "hi"}, headers=evil).status_code == 403
        assert client.get("/api/turns", headers=evil).status_code == 403
        local = {"origin": "http://127.0.0.1:8765"}
        assert client.get("/api/turns", headers=local).status_code == 200


def test_api_token_required_when_configured(
    setup: tuple[TestClient, ScriptedStt], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("UTTERLANE_API_TOKEN", "s3cret-token")
    cfg = LiveConfig()
    cfg.data_dir = tmp_path / "tok"
    cfg.static_dir = tmp_path / "no-ui"
    engines = Engines(
        vad_factory=lambda: EnergyVad(0.02),
        stt=ScriptedStt(),
        embedder=MockEmbedder(),
        llm=MockLlm(),
        tts=MockTts(),
        status={},
    )
    hw = Hardware(cuda=False, gpu_name=None, cpu_count=8, system="Test", machine="x86_64")
    with TestClient(create_app(cfg, engines, hw)) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/turns").status_code == 401
        ok = client.get("/api/turns", headers={"Authorization": "Bearer s3cret-token"})
        assert ok.status_code == 200
        forged = client.get("/api/turns", headers={"origin": "http://127.0.0.1:8765"})
        assert forged.status_code == 401  # a same-origin header alone is not proof
        assert (
            client.get("/auth", params={"token": "wrong"}, follow_redirects=False).status_code
            == 401
        )
        login = client.get("/auth", params={"token": "s3cret-token"}, follow_redirects=False)
        assert login.status_code == 303 and "utterlane_token" in login.headers["set-cookie"]
        assert "httponly" in login.headers["set-cookie"].lower()
        assert client.get("/api/turns").status_code == 200  # cookie now held by the client
