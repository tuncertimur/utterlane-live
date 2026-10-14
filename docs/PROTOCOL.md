# Utterlane Live — client/server protocol (v1)

One local backend (`utterlane run`, or `python -m utterlane_live`) serves the browser UI
(source in `web/`, built into `src/utterlane_live/web`) from the same origin. Everything runs
on the local machine; the only network call is the brain you configure (OpenAI, OpenClaw,
Hermes Agent, a local model server or a webhook), made by the backend.

Default address: `http://127.0.0.1:8765` (UI at `/`, WebSocket at `/ws/live`, MCP at `/mcp`).
Requests carrying a foreign `Origin` are rejected. When `UTTERLANE_API_TOKEN` is set, every
request, the WebSocket included, needs `Authorization: Bearer <token>` or the cookie set by
`GET /auth?token=<token>`.

## 1. WebSocket `/ws/live`

One active socket at a time: a newer tab takes over and the older socket is closed with code
4000 (`replaced`) after a `notice` with `code: "replaced"`. The server owns all conversation
state; the client only captures audio, plays assistant audio and renders events. If the socket
drops, the client reconnects and the server resends `ready`, `speakers` and the recent
`history`.

### 1.1 Client → server

**Binary frames: microphone audio.** Raw PCM, signed 16-bit little-endian, mono,
16 000 Hz. Recommended frame: 20 ms = 320 samples = 640 bytes. Any even byte length is
accepted. No header.

**JSON messages**

| `type` | Fields | Meaning |
|---|---|---|
| `hello` | `client` (string), `version` (1), `sample_rate` (16000) | First message after connect. |
| `playback` | `state`: `"started"` \| `"ended"` \| `"stopped"`, `reply_id` | Assistant audio playback progress, used for echo gating and barge-in. `ended` = all chunks of the reply finished playing; `stopped` = the client stopped it (user Stop, or audio not yet allowed) and the server cancels the rest of that reply. Clients should report playback: the voice-based echo filter only applies while the assistant is audible. |
| `set_mode` | `mode`: `"smart"` \| `"always"` \| `"wake"` | Response mode. `smart`: one person present → answer everything; several people → answer only when addressed. `always`: answer every turn. `wake`: only when the assistant's name or "Utterlane" is said. |
| `listen` | `enabled` (bool) | Pause/resume listening (mute). |
| `rename_speaker` | `speaker_id`, `name` (string, empty = clear name) | Manual correction from the UI. |
| `forget_speaker` | `speaker_id` | Remove the speaker's voice from this session (and its stored profile if any). |
| `reset_session` | — | Clear conversation history and session speakers. Stored profiles stay. |
| `ping` | `t` | Keep-alive; the server answers `pong`. |

### 1.2 Server → client

**Binary frames: assistant audio.** 16-byte little-endian header followed by PCM16LE mono
samples:

| Offset | Type | Field |
|---|---|---|
| 0 | 4 bytes ASCII | magic `UTA1` |
| 4 | uint32 | `reply_seq` (numeric id of the reply, see `assistant_start.reply_seq`) |
| 8 | uint32 | `chunk_index` (0, 1, 2 …) |
| 12 | uint32 | `sample_rate` (24000 for Pocket TTS) |
| 16 | int16[] | samples |

Chunks of one reply must be played back-to-back in `chunk_index` order without gaps.
An `audio_end` JSON message tells the client that no more chunks will come for a reply.

**JSON messages**

| `type` | Fields |
|---|---|
| `ready` | `session_id`, `assistant_name`, `language` (`"tr"` \| `"en"`), `mode`, `listening` (bool), `models` (object: `vad`, `stt`, `embedding`, `tts`, `llm` → `{ "id": str, "status": "ready"\|"loading"\|"error"\|"mock"\|"off", "detail"?: str }`), `budget` (`spent_usd`, `cap_usd`) |
| `status` | `state`: `"loading"` \| `"listening"` \| `"hearing"` \| `"thinking"` \| `"speaking"` \| `"paused"`, `detail`? |
| `level` | `rms` (0..1, microphone level, ≤ 10 per second), `speech` (bool) |
| `history` | `turns`: array of Turn objects, `replies`: array of `{reply_id, in_reply_to, text, interrupted, at}`. Sent after `ready` on (re)connect and after a reset. It is the authoritative recent window: empty arrays clear the timeline. |
| `turn` | `turn`: Turn |
| `turn_update` | `turn_id`, `speaker`: Speaker (a turn was re-attributed, e.g. after an introduction or a correction) |
| `speakers` | `speakers`: array of Speaker (full roster, sent whenever it changes) |
| `identity_event` | `kind`: `"new_speaker"` \| `"named"` \| `"provisional"` \| `"confirmed"` \| `"corrected"` \| `"merged"` \| `"profile_saved"` \| `"consent_declined"` \| `"profile_recognized"` \| `"forgotten"`, `speaker_id`, `name`?, `message` (human readable, in the session language) |
| `assistant_start` | `reply_id` (string), `reply_seq` (uint32), `in_reply_to` (turn id or null) |
| `assistant_delta` | `reply_id`, `text` (incremental text) |
| `audio_end` | `reply_id`, `reply_seq`, `chunks` (total count) |
| `assistant_end` | `reply_id`, `text` (full text actually produced), `interrupted` (bool), `usage`? (`input_tokens`, `output_tokens`, `cost_usd`), `timing`? (seconds: `listen_pipeline_s`, `llm_first_token_s`, `first_text_s`, `first_audio_s`, measured from the end of the user turn unless noted) |
| `assistant_silent` | `in_reply_to`, `reason`: `"not_addressed"` (group conversation, or the brain chose silence) \| `"wake_word_missing"` \| `"no_brain"` (listen-only) \| `"speaker_not_allowed"` (`UTTERLANE_BRAIN_SPEAKERS` policy) |
| `stop_playback` | `reply_id`, `reason`: `"barge_in"` \| `"superseded"` \| `"reset"` \| `"error"` (clients stop for any reason) |
| `usage` | `spent_usd`, `cap_usd`, `calls` |
| `notice` | `level`: `"info"` \| `"warn"` \| `"error"`, `code`, `message` (in the session language) |
| `pong` | `t` |

**Turn object**

```json
{
  "id": "t-000012",
  "speaker": { "...Speaker..." },
  "text": "Ben Tuncer, bugün hava nasıl olacak?",
  "started_at": 1739999999.12,
  "ended_at": 1739999901.80,
  "duration_s": 2.68,
  "stt_ms": 310,
  "attribution": {
    "state": "known",
    "score": 0.71,
    "margin": 0.33
  },
  "flags": ["self_intro"]
}
```

`attribution.state` is one of `known` (matched an existing speaker), `new` (opened a new
speaker), `ambiguous` (could not decide; the turn is not attributed), `short` (too short
to judge reliably; attributed to the previous speaker only as a guess) and `overlap`.
`flags` may contain `self_intro`, `nomination`, `correction`, `consent_yes`, `consent_no`,
`addressed_assistant` and `barge_in`.

**Speaker object**

```json
{
  "id": "spk-3",
  "label": "3. kişi",
  "name": null,
  "name_state": "none",
  "ordinal": 3,
  "persistent": false,
  "present": true,
  "turns": 4,
  "color": 2
}
```

- `label` is what the UI shows: the name, the name with a question mark for a provisional
  name (`"Ahmet?"`), or the ordinal (`"3. kişi"` in Turkish, `"Person 3"` in English).
- `name_state` is one of `none`, `provisional` and `confirmed`.
- `persistent` is true when the voice is stored on this machine with consent.
- `color` is a stable palette index (0–7).
- The ambiguous pseudo-speaker has `id: "unknown"`, `label: "Belirsiz"` / `"Unclear"`,
  `ordinal: 0`, `color: -1`.
- Timestamps (`started_at`, `created_at`, …) are Unix epoch seconds.
- The assistant never appears in `speakers`.

## 2. REST

UI endpoints:

| Method + path | Body / result |
|---|---|
| `GET /api/health` | `{ "status": "ok"\|"degraded"\|"loading", "version", "language", "models": {…same as ready.models…}, "hardware", "gpu": { "name", "available" }, "profiles_key_storage": "os_credential_store"\|"file" }` |
| `GET /api/profiles` | `[{ "id", "name", "created_at", "updated_at", "samples" }]`. Never returns voice vectors. |
| `DELETE /api/profiles/{id}` | Deletes one stored voice profile. |
| `DELETE /api/profiles` | Deletes all stored voice profiles. |
| `GET /api/settings` | `{ "mode", "assistant_name", "language", "budget_cap_usd", "tts_voice", "brain" }` |
| `PUT /api/settings` | `mode` and/or `budget_cap_usd` (0–50); returns the settings. |
| `GET /api/usage` | `{ "spent_usd", "cap_usd", "calls" }` |

Integration endpoints (`/api/state`, `/api/speakers`, `/api/turns`, `/api/turns/wait`,
`/api/say`, `/api/mode`, `/api/listen`, `/api/speakers/{id}/name`) and the MCP tools are
described in [INTEGRATIONS.md](INTEGRATIONS.md).

## 3. Privacy rules

- Audio frames, transcripts and voice vectors stay on the machine. Only conversation
  text (speaker labels + transcripts of the current session) is sent to the brain.
- Voice profiles are stored only after a clear spoken yes to the consent question (the
  speaker's next turn, within 30 seconds), encrypted with a key held in the OS credential
  store, and can be deleted from the UI.
- A voice match is a convenience label, never an authorization: it does not unlock
  personal memory or actions.
