# Contributing

Thanks for helping. Bug reports with `utterlane doctor` output are the most useful thing you can
send; please remove anything personal from logs first.

## Development setup

```bash
git clone https://github.com/tuncertimur/utterlane-live
cd utterlane-live
./install.sh --cpu --skip-models          # Windows: powershell -File install.ps1 -Cpu -SkipModels -NoShortcut
.tools/uv pip install --python .venv -e ".[dev]" --torch-backend cpu
```

(On macOS leave out `--torch-backend cpu`.)

Checks, the same ones CI runs on Linux, macOS and Windows:

```bash
.venv/bin/ruff check src tests scripts examples
.venv/bin/ruff format --check src tests scripts examples
.venv/bin/mypy
.venv/bin/python -m pytest
.venv/bin/python scripts/smoke.py          # starts the app in mock mode, checks REST + MCP
.venv/bin/python scripts/check_release.py  # no secrets, personal paths, audio or weights
```

The tests use scripted speech-to-text, a mock speaker model and a mock brain, so they need no
models, no GPU and no network.

`utterlane run --mock` starts the whole app the same way, which is handy for UI work.

### Web UI

```bash
cd web
npm ci
npm test
npm run build      # writes the production build into src/utterlane_live/web
```

The built UI is committed so that people can run the app without Node.js. Commit the rebuilt
`src/utterlane_live/web` together with your `web/src` change.

## Ground rules

- **Never commit audio, voice prints, model weights, `.env` files or keys.** `.gitignore` and
  `scripts/check_release.py` guard against it; run the scanner before you push.
- Test recordings of real people need their consent and stay out of the repository.
- New behaviour comes with a test. Speaker-identification changes need numbers: describe what
  you measured and on which recordings.
- Keep claims in docs measurable. If a number is in the README, say how it was measured.

## Adding a language

1. `src/utterlane_live/naming_<code>.py`: self-introduction, nomination ("X, go ahead"),
   correction, consent and question parsers (see `naming_en.py`), plus tests.
2. Dispatch to it in `naming.py`.
3. UI strings and the assistant instructions in `i18n.py`.
4. A default voice in `voices.py` (Pocket TTS also speaks de, fr, es, it, pt and nl).
5. Add the code to `Language` and the `--language` choices.
