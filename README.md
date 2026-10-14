<!-- mcp-name: io.github.tuncertimur/utterlane-live -->
<h1 align="center">Utterlane Live</h1>

<p align="center"><b>Give your AI agent ears that know who's talking.</b></p>

<p align="center">
A local, hands-free voice front-end. It listens to the room, knows <i>who</i> said <i>what</i>,
and answers out loud — with OpenAI, your <b>OpenClaw</b> or <b>Hermes Agent</b>, a local model, or anything
OpenAI-compatible. Speech recognition, speaker identification and the voice all run on your own computer.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="docs/INTEGRATIONS.md">Connect your agent</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#privacy-and-safety">Privacy</a> ·
  <a href="#türkçe">Türkçe</a>
</p>

<!-- DEMO-VIDEO -->

---

Voice assistants assume there is one user. Real rooms have several. Utterlane Live keeps
track of the people talking — by name when they introduce themselves, as "Person 3" when
they don't — so your agent knows who asked, who answered, and when to stay quiet.

## Features

- **Hands-free conversation** — voice activity detection, local Whisper speech-to-text,
  streaming local text-to-speech, interrupt it any time by talking (barge-in).
- **Speaker naming from speech** — "I'm Tuncer" names a voice; "Ahmet, go ahead" makes the
  next new voice *Ahmet?* (confirmed after a couple of turns); "I'm not Ahmet, I'm Mehmet"
  corrects it, retroactively. Voices that never introduce themselves become *Person 3*,
  *Person 4*…
- **Consent-first voice memory** — it asks *"Would you like me to recognize your voice next
  time?"*; only a spoken *yes* stores the voice print, AES-GCM encrypted with a key kept in
  your OS keychain. Delete any time.
- **Group etiquette** — alone, it answers everything; in a group it only speaks when
  addressed ("Lena, …"), and never cuts in when you hand the floor to someone else.
- **Bring your own brain** — OpenAI, OpenClaw, Hermes Agent, Ollama, LM Studio, any
  OpenAI-compatible server, or your own webhook. Or none at all (listen-only).
- **MCP server built in** — any MCP client (OpenClaw, Hermes, Claude, Cursor, Open WebUI…)
  can read the room (`recent_turns`, `wait_for_next_turn`, `list_speakers`) and speak
  (`speak`).
- **Only introduced people can command your agent** — configurable (`all`, `named`,
  `enrolled`), on by default for tool-using agents.
- **Runs on ordinary computers** — Windows 10/11 (x64), macOS 14+ on Apple Silicon and Linux
  (x86_64); an NVIDIA GPU makes it faster but is not required. Turkish and English.

## Quick start

**Windows** — download the ZIP (or `git clone`), then double-click **`Utterlane.cmd`**.
The first run installs everything into that folder and opens the app.

```powershell
git clone https://github.com/tuncertimur/utterlane-live
cd utterlane-live
.\Utterlane.cmd
```

**macOS / Linux**

```bash
git clone https://github.com/tuncertimur/utterlane-live
cd utterlane-live
./utterlane.sh
```

The installer puts a private copy of [uv](https://github.com/astral-sh/uv) and Python 3.12
inside the folder (nothing system-wide), picks the CUDA or CPU build automatically, and
downloads the models it needs, hash-verified: about 1 GB on a CPU-only computer, about 2 GB
with an NVIDIA GPU (see [docs/MODELS.md](docs/MODELS.md)). Then open
`http://127.0.0.1:8765`, press **Start** once (browsers need one click for audio) and talk.

To give it a brain, copy `.env.example` to `.env` and choose one block — for example
`OPENAI_API_KEY=…`, or `UTTERLANE_BRAIN=ollama` with `UTTERLANE_BRAIN_MODEL=qwen3:8b` for a
free, fully local setup. Without a brain it still transcribes and labels speakers.

```bash
utterlane --language en        # English (default: Turkish)
utterlane --voice george       # pick a voice
utterlane doctor               # check the installation
```

## Connect your agent

| Your agent | Use it as the brain (`.env`) | Give it the room as tools (MCP) |
|---|---|---|
| **OpenClaw** | `UTTERLANE_BRAIN=openclaw` + gateway token | `openclaw mcp add utterlane --url http://127.0.0.1:8765/mcp --transport streamable-http` |
| **Hermes Agent** | `UTTERLANE_BRAIN=hermes` + API server key | `mcp_servers: {utterlane: {url: "http://127.0.0.1:8765/mcp"}}` |
| **Claude Code** | — | `claude mcp add --transport http utterlane http://127.0.0.1:8765/mcp` |
| **Ollama / LM Studio** | `UTTERLANE_BRAIN=ollama` / `lmstudio` | — |
| **Anything else** | `UTTERLANE_BRAIN=chat` / `responses` / `webhook` | any MCP client, or the REST API |

An [Agent Skill](skills/utterlane-live/SKILL.md) teaches agents how to use the room:
`npx skills add tuncertimur/utterlane-live -g -a openclaw hermes-agent claude-code codex`.
Full setup: **[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)**.

## How it works

```
 browser mic ──► 16 kHz PCM ──► VAD (Silero) ──► one turn
                                                  ├─► Whisper (local GPU/CPU) ──────► text
                                                  └─► voice print (TitaNet-L + ResNet34, local)
                                                        └─► who? known / new "Person N" / unclear
 "I'm Tuncer", "Ahmet, go ahead", "I'm not Ahmet" ──► names (text) + consent ──► encrypted profiles
 [Speaker: Tuncer] text ──► brain (OpenAI / OpenClaw / Hermes / local / webhook) ──► reply
 reply ──► sentence chunks ──► Pocket TTS (local, streaming) ──► browser speaker (barge-in aware)
                     MCP / REST: recent_turns · wait_for_next_turn · list_speakers · speak
```

- One microphone, several people: each turn is embedded with two speaker models and matched
  against the people heard so far; a fast hand-off inside one segment is split at the
  speaker change.
- Uncertain? It says *Unclear* instead of guessing. Very short turns ("yes", "hm") are
  attributed to the previous speaker as a marked guess.
- Your agent never hears audio — only text with speaker labels.

## Performance (measured)

Measured on consented recordings of two Turkish speakers plus public FLEURS voices
(Windows 11, i9-10900X, RTX 3080). Your room and microphone will differ.

| | Result |
|---|---|
| Speaker labels on real voices, real models | 13/13 turns right (two named people + two unknown voices) |
| 30 randomized multi-speaker sessions | 93.6 % right, 1.7 % "unclear"; the two named people were never confused |
| Turkish speech-to-text, Whisper large-v3-turbo (GPU) | WER 0.11 · 0.34 s per turn |
| …same model on CPU (int8, 8 threads) | WER 0.11 · real-time factor 0.6 |
| …Whisper small on CPU | WER 0.27 · real-time factor 0.23 |
| Time from end of speech to first spoken reply (GPU + gpt-6-luna) | 2.0–2.6 s |

The model is chosen automatically: `large-v3-turbo` with an NVIDIA GPU or a CPU with
≥ 16 threads, otherwise `small` (set `UTTERLANE_STT_MODEL` to override).

## Privacy and safety

- Audio, transcripts and voice prints stay on your computer. A brain only receives
  conversation text with speaker labels.
- Voice prints are stored only after a spoken *yes*, encrypted (AES-256-GCM, key in Windows
  Credential Manager / macOS Keychain / Secret Service), and can be deleted in the UI or with
  `utterlane profiles delete-all`.
- **A voice match is a label, not authentication.** Recordings or cloned voices can fool it.
  Don't let it unlock anything sensitive; keep tool-using agents on a restricted profile.
- The server listens on `127.0.0.1` only. Requests from other websites are rejected; set
  `UTTERLANE_API_TOKEN` before exposing it on a network.

## Configuration

Everything is optional; see [`.env.example`](.env.example). Common settings:

| Variable | Default | |
|---|---|---|
| `UTTERLANE_LANGUAGE` | `tr` | `tr` or `en` |
| `UTTERLANE_BRAIN` | `openai` if `OPENAI_API_KEY` is set, else `none` | `openai`, `openclaw`, `hermes`, `ollama`, `lmstudio`, `chat`, `responses`, `webhook`, `none` |
| `UTTERLANE_BRAIN_SPEAKERS` | `all` (`named` for agents) | who may trigger replies |
| `UTTERLANE_MODE` | `smart` | `smart`, `always`, `wake` |
| `UTTERLANE_ASSISTANT_NAME` | `Lena` | the wake name |
| `UTTERLANE_TTS_VOICE` | `female_1` (tr) / `alba` (en) | see [docs/MODELS.md](docs/MODELS.md) |
| `UTTERLANE_STT_MODEL` | `auto` | `large-v3-turbo`, `small` |
| `UTTERLANE_PORT` | `8765` | |

## Limitations

- Two people talking at the same time can't be separated with one microphone; such turns
  may be marked unclear.
- Unknown voices that sound alike can be merged into one "Person N".
- Thresholds were calibrated on studio-like recordings; a noisy room may need tuning
  (`config.SpeakerThresholds`).
- Turkish and English today. Pocket TTS also speaks German, French, Spanish, Italian,
  Portuguese and Dutch, but naming phrases are only implemented for Turkish and English.

## Türkçe

**Utterlane Live**, odadaki konuşmayı yerelde dinleyen, **kimin** konuştuğunu bilen ve sesli
cevap veren bir asistan ön yüzüdür. "Ben Tuncer" dediğinizde sizi tanır, "Ahmet sen konuş"
denince sonraki yeni sesi "Ahmet?" olarak etiketler, kendini tanıtmayan sese "3. kişi" der.
Sesinizi yalnızca sesli onayınızla, şifreli olarak bu bilgisayarda saklar. OpenAI, OpenClaw,
Hermes Agent, Ollama gibi bir "beyne" bağlanabilir ya da MCP ile ajanlara araç olarak açılır.
Kurulum: Windows'ta `Utterlane.cmd`, macOS/Linux'ta `./utterlane.sh`. Varsayılan dil Türkçedir.

## License

[Apache-2.0](LICENSE) © 2026 Tuncer Timur. Models are downloaded separately and keep their own
licenses (MIT, CC-BY-4.0) — see [NOTICE](NOTICE) and [docs/MODELS.md](docs/MODELS.md).
