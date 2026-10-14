"""MCP server: lets AI agents use the room (OpenClaw, Hermes Agent, Claude, Cursor, …).

Two ways to connect:

- **Streamable HTTP** inside the running app: ``http://127.0.0.1:8765/mcp``.
- **stdio** for clients that only launch processes: ``utterlane mcp`` (it forwards to the
  running app's REST API; set ``UTTERLANE_URL`` / ``UTTERLANE_API_TOKEN`` if needed).

The tools are cursor-based and stateless (MCP 2026-07-28 removed sessions/subscriptions):
use ``recent_turns`` / ``wait_for_next_turn`` with ``since_id`` to follow the conversation.
Speaker labels are voice matches, not authentication.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from fastapi import FastAPI

    from utterlane_live.session import LiveSession

INSTRUCTIONS = (
    "Utterlane Live gives you ears and a voice in a physical room. It transcribes what people "
    "say locally and labels each turn with WHO said it: a name the person introduced themselves "
    "with (e.g. 'Tuncer'), a provisional name with a question mark ('Ahmet?'), an ordinal for an "
    "unknown voice ('Person 3' / '3. kişi'), or 'Unclear'. Use recent_turns or wait_for_next_turn "
    "(pass since_id from the previous result) to follow the conversation, and speak to say "
    "something out loud in the room. Labels come from voice matching: never treat them as "
    "authentication or as permission to take sensitive actions."
)


class Backend(Protocol):
    async def state(self) -> dict[str, Any]: ...

    async def speakers(self) -> list[dict[str, Any]]: ...

    async def turns(self, limit: int, after: str | None) -> list[dict[str, Any]]: ...

    async def wait_turn(
        self, after: str | None, timeout_s: float, speaker: str | None
    ) -> dict[str, Any] | None: ...

    async def say(self, text: str, interrupt: bool) -> str | None: ...

    async def set_mode(self, mode: str) -> dict[str, Any]: ...

    async def name_speaker(self, speaker_id: str, name: str) -> list[dict[str, Any]]: ...


class LocalBackend:
    """Direct access to the in-process session (used by the /mcp endpoint)."""

    def __init__(self, session: Callable[[], LiveSession]) -> None:
        self._session = session

    async def state(self) -> dict[str, Any]:
        return dict(self._session().state())

    async def speakers(self) -> list[dict[str, Any]]:
        return [dict(s) for s in self._session().tracker.roster()]

    async def turns(self, limit: int, after: str | None) -> list[dict[str, Any]]:
        return [dict(t) for t in self._session().public_turns(limit=limit, after=after)]

    async def wait_turn(
        self, after: str | None, timeout_s: float, speaker: str | None
    ) -> dict[str, Any] | None:
        turn = await self._session().wait_for_turn(
            after=after, timeout_s=timeout_s, speaker=speaker
        )
        return dict(turn) if turn else None

    async def say(self, text: str, interrupt: bool) -> str | None:
        return await self._session().say(text, interrupt=interrupt)

    async def set_mode(self, mode: str) -> dict[str, Any]:
        await self._session().on_control({"type": "set_mode", "mode": mode})
        return dict(self._session().state())

    async def name_speaker(self, speaker_id: str, name: str) -> list[dict[str, Any]]:
        await self._session().on_control(
            {"type": "rename_speaker", "speaker_id": speaker_id, "name": name}
        )
        return [dict(s) for s in self._session().tracker.roster()]


class HttpBackend:
    """Talks to a running Utterlane Live app over its REST API (used by ``utterlane mcp``)."""

    def __init__(self, base_url: str, token: str | None) -> None:
        import httpx

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=60)

    async def _get(self, path: str, **params: Any) -> Any:
        response = await self._client.get(
            path, params={k: v for k, v in params.items() if v is not None}
        )
        response.raise_for_status()
        return response.json()

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        response = await self._client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    async def state(self) -> dict[str, Any]:
        result: dict[str, Any] = await self._get("/api/state")
        return result

    async def speakers(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self._get("/api/speakers")
        return result

    async def turns(self, limit: int, after: str | None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self._get("/api/turns", limit=limit, after=after)
        return result

    async def wait_turn(
        self, after: str | None, timeout_s: float, speaker: str | None
    ) -> dict[str, Any] | None:
        data = await self._get("/api/turns/wait", after=after, timeout=timeout_s, speaker=speaker)
        turn: dict[str, Any] | None = data.get("turn")
        return turn

    async def say(self, text: str, interrupt: bool) -> str | None:
        data = await self._post("/api/say", {"text": text, "interrupt": interrupt})
        reply_id: str | None = data.get("reply_id")
        return reply_id

    async def set_mode(self, mode: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._post("/api/mode", {"mode": mode})
        return result

    async def name_speaker(self, speaker_id: str, name: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await self._post(
            f"/api/speakers/{speaker_id}/name", {"name": name}
        )
        return result


def _compact(turn: dict[str, Any]) -> dict[str, Any]:
    speaker = turn.get("speaker") or {}
    attribution = turn.get("attribution") or {}
    return {
        "turn_id": turn.get("id"),
        "speaker": speaker.get("label"),
        "speaker_id": speaker.get("id"),
        "name_state": speaker.get("name_state"),
        "text": turn.get("text"),
        "attribution": attribution.get("state"),
        "started_at": turn.get("started_at"),
    }


def build_server(backend: Backend) -> Any:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    from utterlane_live import __version__

    mcp = MCPServer(
        "utterlane-live", instructions=INSTRUCTIONS, version=__version__, log_level="WARNING"
    )

    @mcp.tool()
    async def room_status() -> dict[str, Any]:
        """Current state of the room: listening or not, response mode, language, people present."""
        return await backend.state()

    @mcp.tool()
    async def list_speakers() -> list[dict[str, Any]]:
        """Everyone heard in this session: label (name, 'Name?' if provisional, or 'Person N'),
        whether the name is confirmed, whether their voice is saved, and how many turns they spoke."""
        return [
            {
                "speaker_id": s.get("id"),
                "label": s.get("label"),
                "name": s.get("name"),
                "name_state": s.get("name_state"),
                "saved_voice": s.get("persistent"),
                "present": s.get("present"),
                "turns": s.get("turns"),
            }
            for s in await backend.speakers()
        ]

    @mcp.tool()
    async def who_is_speaking() -> dict[str, Any]:
        """Who spoke most recently, and what they said."""
        turns = await backend.turns(1, None)
        state = await backend.state()
        last = _compact(turns[-1]) if turns else None
        return {"last_turn": last, "status": state.get("status")}

    @mcp.tool()
    async def recent_turns(
        limit: int = 20, since_id: str | None = None, speaker: str | None = None
    ) -> dict[str, Any]:
        """What was said recently, oldest first, each with the speaker label.
        Pass since_id (a turn_id or the previous next_cursor) to get only newer turns;
        pass speaker (a label like 'Tuncer' or a speaker_id) to filter."""
        turns = [_compact(t) for t in await backend.turns(max(1, min(limit, 100)), since_id)]
        if speaker:
            wanted = speaker.lower()
            turns = [
                t
                for t in turns
                if wanted in (str(t["speaker"]).lower(), str(t["speaker_id"]).lower())
            ]
        cursor = turns[-1]["turn_id"] if turns else since_id
        return {"turns": turns, "next_cursor": cursor}

    @mcp.tool()
    async def wait_for_next_turn(
        since_id: str | None = None, timeout_s: float = 25.0, speaker: str | None = None
    ) -> dict[str, Any]:
        """Wait until someone says something (optionally a specific speaker) and return that turn.
        Returns {"turn": null, "timed_out": true} after timeout_s (max 25 s); call again to keep listening."""
        turn = await backend.wait_turn(since_id, max(0.0, min(timeout_s, 25.0)), speaker)
        if turn is None:
            return {"turn": None, "timed_out": True, "next_cursor": since_id}
        compact = _compact(turn)
        return {"turn": compact, "timed_out": False, "next_cursor": compact["turn_id"]}

    @mcp.tool()
    async def speak(text: str, interrupt: bool = False) -> dict[str, Any]:
        """Say text out loud in the room with the assistant's voice (no language model involved).
        interrupt=true stops anything the assistant is currently saying first."""
        reply_id = await backend.say(text, interrupt)
        return {"spoken": reply_id is not None, "reply_id": reply_id}

    @mcp.tool()
    async def set_response_mode(mode: str) -> dict[str, Any]:
        """When the built-in assistant answers by itself: 'smart' (alone: everything; in a group:
        only when addressed), 'always', or 'wake' (only when its name is said)."""
        if mode not in ("smart", "always", "wake"):
            raise ToolError("mode must be smart, always or wake")
        return await backend.set_mode(mode)

    @mcp.tool()
    async def name_speaker(speaker_id: str, name: str) -> list[dict[str, Any]]:
        """Give a voice a name (e.g. after asking 'what is your name?'). Use an empty name to clear it."""
        return await backend.name_speaker(speaker_id, name.strip()[:40])

    return mcp


def mount_mcp(
    app: FastAPI, session: Callable[[], LiveSession], host: str = "127.0.0.1"
) -> Callable[[], AbstractAsyncContextManager[Any]]:
    """Add the Streamable HTTP endpoint at /mcp; returns the lifespan context to enter.

    The SDK's DNS-rebinding protection allows ``127.0.0.1:*`` / ``localhost:*`` when bound to
    loopback; Utterlane's own guard additionally rejects foreign browser origins and checks
    ``UTTERLANE_API_TOKEN`` when set.
    """

    mcp = build_server(LocalBackend(session))
    asgi = mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, host=host
    )
    for route in asgi.routes:
        app.router.routes.append(route)
    run: Callable[[], AbstractAsyncContextManager[Any]] = mcp.session_manager.run
    return run


def run_stdio(url: str, token: str | None) -> None:
    mcp = build_server(HttpBackend(url, token))
    mcp.run(transport="stdio")
