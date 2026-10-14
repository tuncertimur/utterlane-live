# Security

## Reporting a vulnerability

Please report security problems privately through GitHub: open the **Security** tab of this
repository and choose **Report a vulnerability**. Please don't open a public issue for them.

Only the latest release receives fixes.

## Security model

Utterlane Live is a local app that listens to a room. This is what it protects, and what it
does not.

| Area | Behaviour |
|---|---|
| Network | Binds to `127.0.0.1` by default. Browser requests from other websites (foreign `Origin`) are rejected with 403, WebSockets included. The MCP endpoint additionally rejects foreign `Host` headers (DNS rebinding). |
| API token | With `UTTERLANE_API_TOKEN` set, every REST, MCP and WebSocket call needs `Authorization: Bearer <token>`, or the HttpOnly `SameSite=Strict` cookie that `/auth?token=…` sets once for the browser. Set a token before binding to anything other than loopback. |
| Audio | Never leaves the computer. A brain receives conversation text with speaker labels, nothing else. |
| Voice prints | Stored only after a spoken *yes*. AES-256-GCM encrypted; the key lives in the OS keychain (Windows Credential Manager, macOS Keychain, Secret Service), or in an owner-only `profile.key` file when no keychain exists. `utterlane profiles delete-all` removes them. |
| Keys | `OPENAI_API_KEY` and brain keys are read from the environment or `.env`, used only by the local server, never sent to the browser, never logged. URLs shown in status output have credentials and query strings removed. |
| Spending | OpenAI usage is capped (`UTTERLANE_BUDGET_CAP_USD`, default 2 USD) and recorded locally. |
| Models | Downloaded from pinned revisions of their official sources and verified with SHA-256 before use. |

### A voice label is not authentication

Speaker identification compares voices. It can be fooled by recordings, by cloned voices or
simply by a similar voice. Never let a speaker label unlock anything sensitive.

Anyone in the room can talk to your agent, and what they say reaches it as text, so spoken
prompt injection is possible ("…ignore your instructions and…"). If the brain is an agent
with tools (OpenClaw, Hermes Agent, …):

- keep `UTTERLANE_BRAIN_SPEAKERS=named` (the default for these agents) or `enrolled`, so only
  people who introduced themselves, or whose voice is saved, can trigger replies;
- give the agent a restricted tool profile for voice sessions;
- require an explicit confirmation for anything irreversible (payments, messages, deleting).

The MCP tools expose transcripts and speaker names of the room to the connected agent. Connect
only agents you trust with that conversation.
