# Integrations

Utterlane Live can work with your AI agent in two directions:

| | **A. Utterlane as ears + mouth for your agent** | **B. Your agent uses Utterlane as a tool (MCP)** |
|---|---|---|
| What happens | Each finished turn ("[Speaker: Tuncer] …") goes to your agent; Utterlane speaks the agent's answer. | Your agent calls tools like `recent_turns`, `wait_for_next_turn`, `speak`. |
| Best for | Talking to your agent hands-free in a room. | Agents that already run elsewhere (chat apps, schedules) and want room awareness. |
| Configure | `.env` in the Utterlane folder (`UTTERLANE_BRAIN=…`). | Your agent's MCP config, URL `http://127.0.0.1:8765/mcp`. |

You can use both at once (e.g. talk to OpenClaw in the room *and* let OpenClaw read the
room from Telegram).

> **Security.** Agent gateways can run tools with your permissions, and a voice match is not
> authentication. For OpenClaw and Hermes, Utterlane therefore only forwards turns from
> people who **introduced themselves** (`UTTERLANE_BRAIN_SPEAKERS=named`). Use `enrolled`
> to require a saved voice, or `all` to allow everyone. Prefer pointing Utterlane at an agent
> profile with limited tools, and keep everything on `127.0.0.1`.

---

## OpenClaw

**A. OpenClaw as the brain** — OpenClaw's Gateway can serve an OpenAI-compatible
`/v1/responses` endpoint (off by default; see
[OpenResponses HTTP API](https://docs.openclaw.ai/gateway/openresponses-http-api)).

```bash
openclaw config set gateway.http.endpoints.responses.enabled true
openclaw gateway restart
openclaw gateway auth-token --show      # copy the token
```

Utterlane `.env`:

```dotenv
UTTERLANE_BRAIN=openclaw
UTTERLANE_BRAIN_API_KEY=<gateway token>
# optional: a specific agent (ideally one with limited tools)
# UTTERLANE_BRAIN_MODEL=openclaw/<agentId>
# UTTERLANE_BRAIN_SESSION=living-room    # stable OpenClaw session key
```

Defaults: URL `http://127.0.0.1:18789/v1`, model `openclaw/default`, only new turns are sent
(OpenClaw keeps the session memory via the `user` field and `x-openclaw-session-key`).

**B. Utterlane as an MCP server for OpenClaw** ([MCP docs](https://docs.openclaw.ai/tools/mcp)):

```bash
openclaw mcp add utterlane --url http://127.0.0.1:8765/mcp --transport streamable-http
openclaw mcp doctor utterlane --probe
```

**Skill** (teaches the agent how to use the room; AgentSkills format):

```bash
npx skills add tuncertimur/utterlane-live -g -a openclaw
# or copy skills/utterlane-live into ~/.openclaw/skills/
```

---

## Hermes Agent (Nous Research)

**A. Hermes as the brain** — enable the
[API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)
in `~/.hermes/.env`, then run `hermes gateway`:

```dotenv
API_SERVER_ENABLED=true
API_SERVER_KEY=<a long random string>
```

Utterlane `.env`:

```dotenv
UTTERLANE_BRAIN=hermes
UTTERLANE_BRAIN_API_KEY=<the same API_SERVER_KEY>
# UTTERLANE_BRAIN_SESSION=living-room    # becomes X-Hermes-Session-Id
```

Defaults: URL `http://127.0.0.1:8642/v1`, model `hermes-agent`, chat completions, server-side
session via `X-Hermes-Session-Id`.

**B. Utterlane as an MCP server for Hermes**
([MCP docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)) —
`~/.hermes/config.yaml`:

```yaml
mcp_servers:
  utterlane:
    url: "http://127.0.0.1:8765/mcp"
    timeout: 60
```

Then `/reload-mcp` in Hermes (`hermes mcp test utterlane` shows what the server answered if
the connection fails). Hermes names the tools `mcp_utterlane_recent_turns` and so on.

**Skill:** `npx skills add tuncertimur/utterlane-live -g -a hermes-agent`, or copy
`skills/utterlane-live` into `~/.hermes/skills/`.

---

## Claude Code, Claude Desktop, Cursor, other MCP clients

```bash
claude mcp add --transport http utterlane http://127.0.0.1:8765/mcp
```

`mcp.json`-style clients (Cursor, LM Studio, …):

```json
{ "mcpServers": { "utterlane": { "url": "http://127.0.0.1:8765/mcp" } } }
```

Clients that only start processes (stdio):

```json
{ "mcpServers": { "utterlane": { "command": "/path/to/utterlane-live/.venv/bin/utterlane", "args": ["mcp"] } } }
```

On Windows the command is `C:\\path\\to\\utterlane-live\\.venv\\Scripts\\utterlane.exe`.
The stdio server forwards to the running app (`UTTERLANE_URL`, default
`http://127.0.0.1:8765`; `UTTERLANE_API_TOKEN` if you set one).

Open WebUI (Admin Settings → External Tools → MCP, Streamable HTTP) and LibreChat
(`librechat.yaml` → `mcpServers: {utterlane: {type: streamable-http, url: …}}`) work the same way.

### With an API token

Only needed if you set `UTTERLANE_API_TOKEN` (for example to reach the app from another
machine). Every client then sends `Authorization: Bearer <token>`:

| Client | How |
|---|---|
| Claude Code | `claude mcp add --transport http utterlane http://127.0.0.1:8765/mcp --header "Authorization: Bearer <token>"` |
| Hermes Agent | `headers: {Authorization: "Bearer <token>"}` under `mcp_servers.utterlane` |
| OpenClaw | an `Authorization` header for the server in Settings → MCP (keep it in OpenClaw's secret storage, not as a plain config value) |
| stdio (`utterlane mcp`) | set `UTTERLANE_API_TOKEN` in the client's environment for the command |
| Browser UI | open `http://127.0.0.1:8765/auth?token=<token>` once |

### MCP tools

| Tool | Description |
|---|---|
| `room_status()` | listening state, response mode, language, people present |
| `list_speakers()` | everyone heard: label, name state, saved voice, turns |
| `who_is_speaking()` | most recent turn and speaker |
| `recent_turns(limit=20, since_id=None, speaker=None)` | transcript, oldest first, with `next_cursor` |
| `wait_for_next_turn(since_id=None, timeout_s=25, speaker=None)` | long-poll for the next turn |
| `speak(text, interrupt=False)` | say text aloud in the room |
| `name_speaker(speaker_id, name)` | name a voice |
| `set_response_mode(mode)` | `smart` / `always` / `wake` for the built-in assistant |

---

## Local models (free, offline): Ollama, LM Studio

```dotenv
UTTERLANE_BRAIN=ollama            # http://127.0.0.1:11434/v1
UTTERLANE_BRAIN_MODEL=qwen3:8b    # any model you have pulled
```

```dotenv
UTTERLANE_BRAIN=lmstudio          # http://127.0.0.1:1234/v1
UTTERLANE_BRAIN_MODEL=<model id shown in LM Studio>
```

Any other OpenAI-compatible server (vLLM, OpenRouter, a proxy):

```dotenv
UTTERLANE_BRAIN=chat              # or "responses" for /v1/responses servers
UTTERLANE_BRAIN_URL=https://example.com/v1
UTTERLANE_BRAIN_MODEL=model-name
UTTERLANE_BRAIN_API_KEY=...
```

---

## Your own code: webhook brain

```dotenv
UTTERLANE_BRAIN=webhook
UTTERLANE_BRAIN_URL=http://127.0.0.1:9000/utterlane
```

Utterlane POSTs:

```json
{
  "session": "utterlane-room",
  "language": "en",
  "instructions": "You are Lena, …",
  "messages": [{"role": "user", "content": "[Speaker: Tuncer] what's on my calendar?"}],
  "latest": "[Speaker: Tuncer] what's on my calendar?"
}
```

Reply with JSON `{"reply": "You have two meetings today."}`, plain text, or a
`text/event-stream` of `data: {"delta": "..."}` lines. An empty reply means "stay silent".
See [`examples/webhook_brain.py`](../examples/webhook_brain.py).

---

## REST API (for scripts)

All endpoints are on `http://127.0.0.1:8765`. If `UTTERLANE_API_TOKEN` is set, send
`Authorization: Bearer <token>`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/health` | | status, models, hardware |
| GET | `/api/state` | | listening, mode, language, people present |
| GET | `/api/speakers` | | roster |
| GET | `/api/turns` | `limit`, `after` | turns, oldest first |
| GET | `/api/turns/wait` | `after`, `timeout` (s, max 60), `speaker` | `{turn, timed_out}` |
| POST | `/api/say` | `{"text": "...", "interrupt": true}` | `{reply_id}` |
| POST | `/api/mode` | `{"mode": "smart"\|"always"\|"wake"}` | state |
| POST | `/api/listen` | `{"enabled": false}` | state |
| POST | `/api/speakers/{id}/name` | `{"name": "Ahmet"}` | roster |
| GET / DELETE | `/api/profiles[/{id}]` | | saved voice profiles |

The live browser protocol (`/ws/live`) is documented in [PROTOCOL.md](PROTOCOL.md).
