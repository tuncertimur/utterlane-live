"""Runtime configuration for Utterlane Live.

Values come from (highest precedence first):

1. process environment variables,
2. a ``.env`` file in the current working directory,
3. ``config.env`` in the data directory,
4. the defaults below.

Only ``UTTERLANE_*`` keys and ``OPENAI_API_KEY`` are ever read from the env files, and
values are never logged.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ResponseMode = Literal["smart", "always", "wake"]
Language = Literal["tr", "en"]
BrainKind = Literal[
    "openai",
    "openclaw",
    "hermes",
    "ollama",
    "lmstudio",
    "chat",
    "responses",
    "webhook",
    "mock",
    "none",
]
BRAIN_KINDS: tuple[str, ...] = (
    "openai",
    "openclaw",
    "hermes",
    "ollama",
    "lmstudio",
    "chat",
    "responses",
    "webhook",
    "mock",
    "none",
)

PACKAGE_DIR = Path(__file__).resolve().parent
_EMPTY_IS_NONE = frozenset({"UTTERLANE_BRAIN_REASONING_EFFORT"})


def default_data_dir() -> Path:
    override = os.environ.get("UTTERLANE_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Utterlane"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Utterlane"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "utterlane"


def _dotenv_value(raw: str) -> str:
    """``"quoted # kept"`` stays whole; ``value  # comment`` loses the comment."""

    value = raw.strip()
    if value[:1] in ('"', "'"):
        end = value.find(value[0], 1)
        if end != -1:
            return value[1:end]
    if value.startswith("#"):
        return ""
    return re.split(r"\s#", value, maxsplit=1)[0].strip()


def read_dotenv(path: Path) -> dict[str, str]:
    """Read only the keys Utterlane Live needs; never logs values."""

    values: dict[str, str] = {}
    try:
        # utf-8-sig: Windows editors may save a byte-order mark.
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return values
    for raw in lines:
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key == "OPENAI_API_KEY" or key.startswith("UTTERLANE_"):
            values[key] = _dotenv_value(value)
    return values


@dataclass(frozen=True, slots=True)
class SpeakerThresholds:
    """Fused-cosine thresholds (TitaNet-L + ResNet34-LM).

    Calibrated on whole turns of consented Turkish recordings plus 150 public FLEURS-tr test clips,
    scoring each turn against the vector of the speaker's self-introduction turn:

    | turn length | same person, worst 1% | two known people, max | unknown voice, max |
    |---|---|---|---|
    | 1–2 s | 0.62 | 0.30 | 0.36 |
    | 2–4 s | 0.72 | 0.30 | 0.35 |
    | 4–8 s | 0.77 | 0.30 | 0.33 |

    Between ``new_speaker`` and ``match`` a turn is reported as ambiguous instead of guessed.
    Room audio, distance and microphones differ, so the values stay configurable.
    """

    match: float = 0.48
    match_short: float = 0.50
    new_speaker: float = 0.40
    margin: float = 0.08
    min_embed_s: float = 0.6
    min_new_s: float = 1.0
    update_min_score: float = 0.55
    merge: float = 0.60
    self_voice: float = 0.50


@dataclass(slots=True)
class BrainConfig:
    """Where replies come from.

    - ``openai``: OpenAI Responses API (``OPENAI_API_KEY``), spend-capped.
    - ``openclaw``: an OpenClaw Gateway (``http://127.0.0.1:18789/v1``, Responses API).
    - ``hermes``: a Hermes Agent API server (``http://127.0.0.1:8642/v1``).
    - ``ollama`` / ``lmstudio``: a local model server (no key, no cost).
    - ``chat`` / ``responses``: any other OpenAI-compatible endpoint (set the URL).
    - ``webhook``: POST each turn to your own HTTP endpoint and speak what it returns.
    - ``none``: no replies (transcription + speaker labels only, e.g. when an agent drives
      the room through MCP).
    """

    kind: BrainKind = "openai"
    api: Literal["responses", "chat", "webhook", "mock", "none"] = "responses"
    model: str = "gpt-6-luna"
    url: str | None = None
    api_key: str | None = None
    history: Literal["full", "latest"] = "full"
    # Who may trigger a reply: everyone, or only speakers with a confirmed name. Agent
    # gateways (OpenClaw, Hermes) default to "named" because they can run tools.
    speakers: Literal["all", "named", "enrolled"] = "all"
    session: str = "utterlane-room"
    reasoning_effort: str | None = "none"
    max_output_tokens: int = 400
    timeout_s: float = 60.0
    budget_cap_usd: float = 2.0
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class LiveConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = field(default_factory=default_data_dir)
    models_dir: Path | None = None  # defaults to <data_dir>/models
    static_dir: Path = PACKAGE_DIR / "web"

    assistant_name: str = "Lena"
    mode: ResponseMode = "smart"
    language: Language = "tr"
    history_turns: int = 40

    brain: BrainConfig = field(default_factory=BrainConfig)

    stt_mode: Literal["whisper", "mock"] = "whisper"
    stt_model: str = "auto"  # auto | large-v3-turbo | medium | small
    stt_device: str = "auto"  # auto | cuda | cpu
    stt_compute_type: str = "auto"
    stt_beam_size: int = 0  # 0 = auto (5 on GPU, 1 on CPU)
    stt_cpu_threads: int = 0

    embedding_mode: Literal["sherpa", "mock"] = "sherpa"
    tts_engine: str = "pocket"  # pocket | none
    tts_voice: str = "default"
    tts_device: str = "auto"

    vad_start_probability: float = 0.5
    vad_end_probability: float = 0.35
    end_silence_ms: int = 650
    preroll_ms: int = 300
    min_speech_ms: int = 300
    max_utterance_s: float = 18.0
    barge_in_ms: int = 350

    speakers: SpeakerThresholds = field(default_factory=SpeakerThresholds)

    @property
    def models_root(self) -> Path:
        return self.models_dir or self.data_dir / "models"

    @property
    def wake_words(self) -> tuple[str, ...]:
        return (self.assistant_name.lower(), "utterlane")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> LiveConfig:
        data_dir = Path(os.environ.get("UTTERLANE_DATA_DIR") or default_data_dir())
        sources = [
            read_dotenv(data_dir / "config.env"),
            read_dotenv(Path.cwd() / ".env"),
            {
                k: v
                for k, v in os.environ.items()
                if k == "OPENAI_API_KEY" or k.startswith("UTTERLANE_")
            },
            env or {},
        ]
        merged: dict[str, str] = {}
        for source in sources:
            # An empty value means "not set" (e.g. `UTTERLANE_PORT=` left blank in .env).
            merged.update({k: v for k, v in source.items() if v.strip() or k in _EMPTY_IS_NONE})
        cfg = cls(data_dir=Path(merged.get("UTTERLANE_DATA_DIR", data_dir)))
        if merged.get("UTTERLANE_MODELS_DIR"):
            cfg.models_dir = Path(merged["UTTERLANE_MODELS_DIR"])
        if merged.get("UTTERLANE_STATIC_DIR"):
            cfg.static_dir = Path(merged["UTTERLANE_STATIC_DIR"])
        cfg.host = merged.get("UTTERLANE_HOST", cfg.host)
        cfg.port = int(merged.get("UTTERLANE_PORT", cfg.port))
        cfg.assistant_name = merged.get("UTTERLANE_ASSISTANT_NAME", cfg.assistant_name)
        cfg.mode = _choice(merged.get("UTTERLANE_MODE"), ("smart", "always", "wake"), cfg.mode)  # type: ignore[assignment]
        cfg.language = _choice(merged.get("UTTERLANE_LANGUAGE"), ("tr", "en"), cfg.language)  # type: ignore[assignment]

        brain = cfg.brain
        openai_key = merged.get("OPENAI_API_KEY") or None
        default_kind = "openai" if openai_key else "none"
        brain.kind = _choice(merged.get("UTTERLANE_BRAIN"), BRAIN_KINDS, default_kind)  # type: ignore[assignment]
        apply_brain_preset(brain, openai_key)
        brain.api_key = merged.get("UTTERLANE_BRAIN_API_KEY") or brain.api_key
        brain.url = merged.get("UTTERLANE_BRAIN_URL") or brain.url
        brain.model = merged.get("UTTERLANE_BRAIN_MODEL", brain.model)
        brain.history = _choice(
            merged.get("UTTERLANE_BRAIN_HISTORY"), ("full", "latest"), brain.history
        )  # type: ignore[assignment]
        brain.speakers = _choice(
            merged.get("UTTERLANE_BRAIN_SPEAKERS"), ("all", "named", "enrolled"), brain.speakers
        )  # type: ignore[assignment]
        brain.session = merged.get("UTTERLANE_BRAIN_SESSION", brain.session)
        effort = merged.get("UTTERLANE_BRAIN_REASONING_EFFORT")
        if effort is not None:
            brain.reasoning_effort = effort or None
        brain.budget_cap_usd = float(merged.get("UTTERLANE_BUDGET_CAP_USD", brain.budget_cap_usd))
        brain.timeout_s = float(merged.get("UTTERLANE_BRAIN_TIMEOUT_S", brain.timeout_s))
        for key, value in merged.items():
            if key.startswith("UTTERLANE_BRAIN_HEADER_"):
                header = key.removeprefix("UTTERLANE_BRAIN_HEADER_").replace("_", "-")
                brain.extra_headers[header] = value
        if brain.kind == "hermes":
            brain.extra_headers.setdefault("X-Hermes-Session-Id", brain.session)
        if brain.kind == "openclaw":
            brain.extra_headers.setdefault("x-openclaw-session-key", brain.session)

        cfg.stt_mode = _choice(merged.get("UTTERLANE_STT_MODE"), ("whisper", "mock"), cfg.stt_mode)  # type: ignore[assignment]
        cfg.stt_model = merged.get("UTTERLANE_STT_MODEL", cfg.stt_model)
        cfg.stt_device = merged.get("UTTERLANE_STT_DEVICE", cfg.stt_device)
        cfg.stt_compute_type = merged.get("UTTERLANE_STT_COMPUTE_TYPE", cfg.stt_compute_type)
        cfg.stt_beam_size = int(merged.get("UTTERLANE_STT_BEAM_SIZE", cfg.stt_beam_size))
        cfg.stt_cpu_threads = int(merged.get("UTTERLANE_STT_CPU_THREADS", cfg.stt_cpu_threads))
        cfg.embedding_mode = _choice(
            merged.get("UTTERLANE_EMBEDDING_MODE"), ("sherpa", "mock"), cfg.embedding_mode
        )  # type: ignore[assignment]
        cfg.tts_engine = merged.get("UTTERLANE_TTS_ENGINE", cfg.tts_engine)
        cfg.tts_voice = merged.get("UTTERLANE_TTS_VOICE", cfg.tts_voice)
        cfg.tts_device = merged.get("UTTERLANE_TTS_DEVICE", cfg.tts_device)
        return cfg


def _choice(value: str | None, allowed: tuple[str, ...], default: str) -> str:
    if value is None or value == "":
        return default
    if value not in allowed:
        raise ValueError(f"invalid setting {value!r}; expected one of {allowed}")
    return value


def apply_brain_preset(brain: BrainConfig, openai_key: str | None) -> None:
    """Fill URL / API style / model / history defaults for the chosen brain kind."""

    presets: dict[str, dict[str, object]] = {
        "openai": {
            "api": "responses",
            "url": None,
            "model": "gpt-6-luna",
            "history": "full",
            "speakers": "all",
        },
        "openclaw": {
            "api": "responses",
            "url": "http://127.0.0.1:18789/v1",
            "model": "openclaw/default",
            "history": "latest",
            "speakers": "named",
            "reasoning_effort": None,
        },
        "hermes": {
            "api": "chat",
            "url": "http://127.0.0.1:8642/v1",
            "model": "hermes-agent",
            "history": "latest",
            "speakers": "named",
            "reasoning_effort": None,
        },
        "ollama": {
            "api": "chat",
            "url": "http://127.0.0.1:11434/v1",
            "model": "",
            "history": "full",
            "speakers": "all",
        },
        "lmstudio": {
            "api": "chat",
            "url": "http://127.0.0.1:1234/v1",
            "model": "",
            "history": "full",
            "speakers": "all",
        },
        "chat": {"api": "chat", "url": None, "model": "", "history": "full", "speakers": "all"},
        "responses": {
            "api": "responses",
            "url": None,
            "model": "",
            "history": "full",
            "speakers": "all",
        },
        "webhook": {
            "api": "webhook",
            "url": None,
            "model": "",
            "history": "latest",
            "speakers": "all",
        },
        "mock": {"api": "mock", "url": None, "model": "mock", "history": "full", "speakers": "all"},
        "none": {"api": "none", "url": None, "model": "", "history": "full", "speakers": "all"},
    }
    for key, value in presets[brain.kind].items():
        setattr(brain, key, value)
    if brain.kind == "openai":
        brain.api_key = openai_key
