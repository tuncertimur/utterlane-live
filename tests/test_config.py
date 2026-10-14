import os
from pathlib import Path

import pytest

from utterlane_live.config import LiveConfig, read_dotenv

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in list(os.environ):
        if key.startswith("UTTERLANE_") or key == "OPENAI_API_KEY":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UTTERLANE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_env_example_copied_unchanged_gives_the_defaults(clean_env: Path) -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    (clean_env / ".env").write_text(example, encoding="utf-8")
    cfg = LiveConfig.from_env()
    assert (cfg.language, cfg.assistant_name, cfg.mode) == ("tr", "Lena", "smart")
    assert cfg.brain.kind == "none" and cfg.brain.api_key is None
    assert cfg.data_dir == clean_env / "data"


def test_dotenv_comments_quotes_bom_export_and_blanks(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (clean_env / ".env").write_text(
        "﻿UTTERLANE_LANGUAGE=en   # English\n"
        "export UTTERLANE_ASSISTANT_NAME='Nova # part of the name'\n"
        "UTTERLANE_BRAIN=ollama\n"
        'UTTERLANE_BRAIN_MODEL="qwen3:8b"\n'
        "UTTERLANE_BRAIN_URL=            # left blank on purpose\n"
        "UTTERLANE_PORT=\n"
        "UTTERLANE_DATA_DIR=\n"
        "NOT_OURS=secret\n",
        encoding="utf-8",
    )
    assert "NOT_OURS" not in read_dotenv(clean_env / ".env")
    monkeypatch.setenv("UTTERLANE_MODE", "")  # an empty variable does not override either
    cfg = LiveConfig.from_env()
    assert cfg.language == "en"
    assert cfg.assistant_name == "Nova # part of the name"
    assert (cfg.brain.kind, cfg.brain.model) == ("ollama", "qwen3:8b")
    assert cfg.brain.url == "http://127.0.0.1:11434/v1"
    assert cfg.port == 8765 and cfg.mode == "smart"
    assert cfg.data_dir == clean_env / "data"


def test_environment_beats_dotenv(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (clean_env / ".env").write_text("UTTERLANE_LANGUAGE=en\n", encoding="utf-8")
    monkeypatch.setenv("UTTERLANE_LANGUAGE", "tr")
    assert LiveConfig.from_env().language == "tr"
    assert LiveConfig.from_env({"UTTERLANE_LANGUAGE": "en"}).language == "en"
