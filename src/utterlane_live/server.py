"""FastAPI app: web UI, live WebSocket, REST API for integrations, and the MCP endpoint.

Security model (local app):
- binds to 127.0.0.1 by default;
- browser requests from foreign origins are rejected (blocks cross-site requests from web
  pages to localhost); non-browser clients (agents, curl) send no Origin header;
- optional ``UTTERLANE_API_TOKEN`` (recommended when binding to a LAN address): REST, MCP
  and the WebSocket then require ``Authorization: Bearer <token>`` or the cookie set by
  opening ``/auth?token=<token>`` once in the browser.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from utterlane_live import __version__
from utterlane_live.audio import EnergyVad, SileroVad
from utterlane_live.config import LiveConfig
from utterlane_live.embedding import MockEmbedder, SherpaFusedEmbedder
from utterlane_live.gpu import Hardware, detect_hardware
from utterlane_live.i18n import t
from utterlane_live.llm import SpendLedger, build_brain
from utterlane_live.models import VAD_FILE, choose_stt
from utterlane_live.profiles import ProfileStore, default_key_provider
from utterlane_live.session import Engines, LiveSession, Transport
from utterlane_live.speakers import SpeakerTracker
from utterlane_live.stt import ScriptedStt, load_whisper
from utterlane_live.tts import MockTts, TtsEngine, WorkerTts
from utterlane_live.voices import ATTRIBUTION, NON_COMMERCIAL_VOICES, worker_spec

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
AUTH_COOKIE = "utterlane_token"


def _status(model_id: str, status: str, detail: str | None = None) -> dict[str, str]:
    out = {"id": model_id, "status": status}
    if detail:
        out["detail"] = detail
    return out


async def build_engines(cfg: LiveConfig, ledger: SpendLedger, hardware: Hardware) -> Engines:
    status: dict[str, dict[str, str]] = {}
    models = cfg.models_root

    silero_path = models / VAD_FILE
    if cfg.stt_mode == "mock" or not silero_path.is_file():
        vad_factory: Any = lambda: EnergyVad(0.02)  # noqa: E731
        status["vad"] = _status("energy-vad", "mock")
    else:
        SileroVad(silero_path)  # verify the pinned hash once
        vad_factory = lambda: SileroVad(silero_path, verify=False)  # noqa: E731
        status["vad"] = _status("silero-vad-v6.2.1", "ready")

    if cfg.stt_mode == "mock":
        stt: Any = ScriptedStt()
        status["stt"] = _status("scripted", "mock")
    else:
        choice = choose_stt(cfg, hardware)
        stt, used = await asyncio.to_thread(
            load_whisper, choice, models, language=cfg.language, cpu_threads=cfg.stt_cpu_threads
        )
        status["stt"] = _status(f"whisper-{used.model.name} ({used.device})", "ready", used.reason)

    if cfg.embedding_mode == "mock":
        embedder: Any = MockEmbedder()
        status["embedding"] = _status("mock", "mock")
    else:
        embedder = await asyncio.to_thread(SherpaFusedEmbedder, models)
        status["embedding"] = _status("titanet-l + resnet34-lm (cpu)", "ready")

    brain, description = build_brain(cfg.brain, ledger, cfg.language)
    await brain.warmup()
    status["llm"] = _status(description, "ready" if brain.mode != "off" else "off")

    tts, status["tts"] = await start_voice(cfg)
    return Engines(
        vad_factory=vad_factory, stt=stt, embedder=embedder, llm=brain, tts=tts, status=status
    )


# Hugging Face's offline-mode error when a model was never downloaded into the cache.
_VOICE_NOT_CACHED = ("LocalEntryNotFoundError", "outgoing traffic has been disabled")


async def start_voice(cfg: LiveConfig) -> tuple[TtsEngine, dict[str, str]]:
    """Start the TTS worker offline; if its voice model is not cached yet, download it once."""

    spec = worker_spec(cfg)
    if spec is None:
        return MockTts(), _status("none", "off")
    for online in (False, True):
        current = worker_spec(cfg, online=True) if online else spec
        assert current is not None
        if online:
            print("Downloading the voice model (one time, about 0.4 GB) …", flush=True)
        worker = WorkerTts(current, cfg.data_dir / "tts-cache")
        try:
            await worker.start()
        except Exception as exc:
            await worker.close()
            if not online and any(mark in str(exc) for mark in _VOICE_NOT_CACHED):
                continue  # e.g. setup was skipped or interrupted
            return MockTts(), _status(spec.engine_id, "error", f"{type(exc).__name__}: {exc}"[:200])
        ready = getattr(worker, "ready_info", None) or {}
        voice = str(ready.get("voice") or spec.voice)
        note = ATTRIBUTION.get(cfg.language, "")
        if ready.get("note"):
            note += f" Note: {ready['note']}."
        if voice in NON_COMMERCIAL_VOICES:
            note += f" Note: the '{voice}' voice is licensed for non-commercial use only."
        return worker, _status(f"{spec.engine_id}:{voice}", "ready", note)
    raise AssertionError("unreachable")


def _origin_allowed(origin: str | None) -> bool:
    if not origin or origin == "null":
        return origin is None
    host = urlparse(origin).hostname or ""
    allowed = {
        o.strip() for o in os.environ.get("UTTERLANE_ALLOW_ORIGINS", "").split(",") if o.strip()
    }
    return host in LOOPBACK_HOSTS or origin in allowed


def create_app(
    cfg: LiveConfig | None = None,
    engines: Engines | None = None,
    hardware: Hardware | None = None,
) -> FastAPI:
    cfg = cfg or LiveConfig.from_env()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    ledger = SpendLedger(cfg.data_dir / "llm-usage.json", cfg.brain.budget_cap_usd)
    token = os.environ.get("UTTERLANE_API_TOKEN") or None
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hw = hardware if hardware is not None else await asyncio.to_thread(detect_hardware)
        loaded = engines or await build_engines(cfg, ledger, hw)
        store = ProfileStore(
            cfg.data_dir / "voice-profiles.bin",
            default_key_provider(cfg.data_dir),
            loaded.embedder.fingerprint,
        )
        result = await asyncio.to_thread(store.load)
        tracker = SpeakerTracker(cfg.speakers, result.profiles, language=cfg.language)
        session = LiveSession(cfg, loaded, tracker, store, ledger)
        session.start()
        state.update(engines=loaded, store=store, session=session, load=result, hardware=hw)
        mcp_lifespan = state.get("mcp_lifespan")
        try:
            if mcp_lifespan is not None:
                async with mcp_lifespan():
                    yield
            else:
                yield
        finally:
            await session.close()
            with contextlib.suppress(Exception):
                await loaded.tts.close()

    app = FastAPI(title="Utterlane Live", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        origin = request.headers.get("origin")
        if origin is not None and not _origin_allowed(origin):
            return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
        path = request.url.path
        protected = path.startswith("/api/") or path.startswith("/mcp")
        if (
            token
            and protected
            and path != "/api/health"
            and not _authorized(request.headers, request.cookies)
        ):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    def _authorized(headers: Any, cookies: Any) -> bool:
        if not token:
            return True
        supplied = headers.get("authorization", "").removeprefix("Bearer ").strip()
        supplied = supplied or cookies.get(AUTH_COOKIE, "")
        return bool(supplied) and hmac.compare_digest(supplied, token)

    @app.get("/auth")
    async def auth(token_param: str = Query("", alias="token")) -> Response:
        """Open http://<host>:<port>/auth?token=<UTTERLANE_API_TOKEN> once to unlock the UI."""
        if not token or not hmac.compare_digest(token_param, token):
            raise HTTPException(401, "invalid_token")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            AUTH_COOKIE, token, httponly=True, samesite="strict", max_age=30 * 86400
        )
        return response

    def session() -> LiveSession:
        current: LiveSession | None = state.get("session")
        if current is None:
            raise HTTPException(503, "starting")
        return current

    # ---------------------------------------------------------------- status
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        loaded: Engines | None = state.get("engines")
        if loaded is None:
            return {"status": "loading", "version": __version__, "models": {}}
        degraded = any(m["status"] == "error" for m in loaded.status.values())
        hw: Hardware = state["hardware"]
        return {
            "status": "degraded" if degraded else "ok",
            "version": __version__,
            "language": cfg.language,
            "models": loaded.status,
            "hardware": hw.describe(),
            "gpu": {"name": hw.gpu_name, "available": hw.cuda},
            "profiles_key_storage": state["store"].key_provider.storage,
        }

    @app.get("/api/state")
    async def room_state() -> dict[str, object]:
        return session().state()

    @app.get("/api/speakers")
    async def speakers() -> list[dict[str, object]]:
        return session().tracker.roster()

    @app.get("/api/turns")
    async def turns(limit: int = 20, after: str | None = None) -> list[dict[str, object]]:
        return session().public_turns(limit=max(1, min(limit, 500)), after=after)

    @app.get("/api/turns/wait")
    async def wait_turn(
        after: str | None = None, timeout: float = 30.0, speaker: str | None = None
    ) -> dict[str, object]:
        timeout = max(0.0, min(timeout, 60.0))
        turn = await session().wait_for_turn(after=after, timeout_s=timeout, speaker=speaker)
        return {"turn": turn, "timed_out": turn is None}

    @app.post("/api/say")
    async def say(body: dict[str, Any]) -> dict[str, object]:
        text = str(body.get("text") or "")
        if not text.strip():
            raise HTTPException(400, "text_required")
        reply_id = await session().say(text, interrupt=bool(body.get("interrupt", True)))
        return {"reply_id": reply_id}

    @app.post("/api/mode")
    async def set_mode(body: dict[str, Any]) -> dict[str, object]:
        mode = body.get("mode")
        if mode not in ("smart", "always", "wake"):
            raise HTTPException(400, "mode_must_be_smart_always_or_wake")
        await session().on_control({"type": "set_mode", "mode": mode})
        return session().state()

    @app.post("/api/listen")
    async def set_listen(body: dict[str, Any]) -> dict[str, object]:
        await session().on_control({"type": "listen", "enabled": bool(body.get("enabled", True))})
        return session().state()

    @app.post("/api/speakers/{speaker_id}/name")
    async def name_speaker(speaker_id: str, body: dict[str, Any]) -> list[dict[str, object]]:
        await session().on_control(
            {
                "type": "rename_speaker",
                "speaker_id": speaker_id,
                "name": str(body.get("name") or ""),
            }
        )
        return session().tracker.roster()

    # -------------------------------------------------------------- profiles
    @app.get("/api/profiles")
    async def list_profiles() -> list[dict[str, object]]:
        return await asyncio.to_thread(state["store"].list_public)

    @app.delete("/api/profiles/{profile_id}")
    async def delete_profile(profile_id: str) -> dict[str, object]:
        current = session()
        deleted = await asyncio.to_thread(state["store"].delete, profile_id)
        if not deleted:
            raise HTTPException(404, "profile_not_found")
        current.tracker.profiles.pop(profile_id, None)
        for speaker in current.tracker.speakers.values():
            if speaker.profile_id == profile_id:
                speaker.profile_id = None
        await current._send_roster()
        return {"deleted": profile_id}

    @app.delete("/api/profiles")
    async def delete_all_profiles() -> dict[str, object]:
        current = session()
        count = await asyncio.to_thread(state["store"].delete_all)
        current.tracker.profiles.clear()
        for speaker in current.tracker.speakers.values():
            speaker.profile_id = None
        await current._send_roster()
        return {"deleted": count}

    # -------------------------------------------------------------- settings
    @app.get("/api/settings")
    async def get_settings() -> dict[str, object]:
        return {
            "mode": session().mode,
            "assistant_name": cfg.assistant_name,
            "language": cfg.language,
            "budget_cap_usd": ledger.cap_usd,
            "tts_voice": cfg.tts_voice,
            "brain": cfg.brain.kind,
        }

    @app.put("/api/settings")
    async def put_settings(body: dict[str, Any]) -> dict[str, object]:
        if body.get("mode") in ("smart", "always", "wake"):
            session().mode = body["mode"]
        cap = body.get("budget_cap_usd")
        if isinstance(cap, (int, float)) and 0 <= cap <= 50:
            ledger.cap_usd = float(cap)
        return await get_settings()

    @app.get("/api/usage")
    async def usage() -> dict[str, float | int]:
        return ledger.public()

    # ------------------------------------------------------------- websocket
    @app.websocket("/ws/live")
    async def live(websocket: WebSocket) -> None:
        if not _origin_allowed(websocket.headers.get("origin")) or not _authorized(
            websocket.headers, websocket.cookies
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        current = session()
        previous: Transport | None = current.transport

        async def send_json(message: dict[str, Any]) -> None:
            await websocket.send_text(json.dumps(message, ensure_ascii=False))

        async def send_bytes(data: bytes) -> None:
            await websocket.send_bytes(data)

        transport = Transport(send_json, send_bytes)
        if previous is not None:
            old_socket = state.get("socket")
            with contextlib.suppress(Exception):
                await previous.send_json(
                    {
                        "type": "notice",
                        "level": "warn",
                        "code": "replaced",
                        "message": t(cfg.language, "replaced"),
                    }
                )
            if old_socket is not None:
                with contextlib.suppress(Exception):
                    await old_socket.close(code=4000, reason="replaced")
        state["socket"] = websocket
        await current.attach(transport)
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await current.on_audio(message["bytes"])
                elif message.get("text") is not None:
                    try:
                        payload = json.loads(message["text"])
                    except ValueError:
                        continue
                    if isinstance(payload, dict):
                        await current.on_control(payload)
        except WebSocketDisconnect:
            pass
        finally:
            await current.detach(transport)

    # ------------------------------------------------------------------- MCP
    try:
        from utterlane_live.mcp_server import mount_mcp

        state["mcp_lifespan"] = mount_mcp(app, session, cfg.host)
    except ImportError:
        pass

    static_dir = cfg.static_dir
    if static_dir.is_dir() and (static_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="ui")
    else:

        @app.get("/")
        async def no_ui() -> HTMLResponse:
            return HTMLResponse(
                "<!doctype html><meta charset=utf-8><title>Utterlane Live</title>"
                "<body style='font-family:system-ui;background:#111;color:#eee;padding:2rem'>"
                "<h1>Utterlane Live</h1><p>The web UI is not built. Run "
                "<code>npm --prefix web ci &amp;&amp; npm --prefix web run build</code>.</p></body>"
            )

    return app
