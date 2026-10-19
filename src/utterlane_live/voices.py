"""Local TTS worker configuration (Pocket TTS in the app's own Python environment)."""

from __future__ import annotations

import sys
from pathlib import Path

from utterlane_live.config import LiveConfig
from utterlane_live.tts import WorkerSpec

DEFAULT_VOICES = {"tr": "female_1", "en": "alba"}
TURKISH_VOICES = frozenset({"female_1", "female_2", "female_3", "male_1", "male_2", "male_3"})
WARMUP_TEXT = {"tr": "Merhaba.", "en": "Hello."}

# kyutai/tts-voices sources with non-commercial licenses (CC-BY-NC-4.0).
NON_COMMERCIAL_VOICES = frozenset({"cosette", "jean"})

ATTRIBUTION = {
    "tr": "Voice: Pocket TTS Turkish by wite-tech (CC-BY-4.0), based on Kyutai Pocket TTS.",
    "en": "Voice: Kyutai Pocket TTS (CC-BY-4.0); voice sources per kyutai/tts-voices.",
}


def hf_home(cfg: LiveConfig) -> Path:
    return cfg.data_dir / "hf"


def worker_spec(cfg: LiveConfig, *, online: bool = False) -> WorkerSpec | None:
    """How to start the TTS worker, or ``None`` when speech output is disabled."""

    if cfg.tts_engine in ("none", "mock", ""):
        return None
    if cfg.tts_engine != "pocket":
        raise ValueError(f"unknown TTS engine {cfg.tts_engine!r} (supported: pocket, none)")
    voice = (
        cfg.tts_voice
        if cfg.tts_voice not in ("", "default")
        else DEFAULT_VOICES.get(cfg.language, "default")
    )
    if voice != "default" and (voice in TURKISH_VOICES) != (cfg.language == "tr"):
        # e.g. UTTERLANE_TTS_VOICE=female_1 (Turkish model) with --language en
        voice = DEFAULT_VOICES.get(cfg.language, "default")
    env = {"HF_HOME": str(hf_home(cfg))}
    if online:
        env["HF_HUB_OFFLINE"] = "0"
    return WorkerSpec(
        engine_id="pocket",
        command=(sys.executable, "-X", "utf8", "-m", "utterlane_live.tts_worker"),
        args=("--language", cfg.language, "--voice", voice, "--device", cfg.tts_device),
        env=env,
        voice=voice,
        startup_timeout_s=900 if online else 240,
        streaming=True,
        warmup_text=WARMUP_TEXT.get(cfg.language, "Hello."),
    )
