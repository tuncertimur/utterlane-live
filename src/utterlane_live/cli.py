"""Command line: ``utterlane [run|setup|doctor|models|mcp|profiles]``."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            try:
                stream.reconfigure(encoding="utf-8")
            except ValueError:
                pass


def _open_when_ready(url: str) -> None:
    deadline = time.time() + 900
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2) as response:
                if (
                    response.status == 200
                    and json.loads(response.read()).get("status") != "loading"
                ):
                    webbrowser.open(url)
                    return
        except Exception:
            pass
        time.sleep(1)


def _apply_overrides(args: argparse.Namespace) -> None:
    mapping = {
        "language": "UTTERLANE_LANGUAGE",
        "voice": "UTTERLANE_TTS_VOICE",
        "brain": "UTTERLANE_BRAIN",
        "port": "UTTERLANE_PORT",
        "host": "UTTERLANE_HOST",
        "stt_model": "UTTERLANE_STT_MODEL",
    }
    for attr, env in mapping.items():
        value = getattr(args, attr, None)
        if value not in (None, ""):
            os.environ[env] = str(value)
    if getattr(args, "mock", False):
        for key, value in {
            "UTTERLANE_STT_MODE": "mock",
            "UTTERLANE_EMBEDDING_MODE": "mock",
            "UTTERLANE_BRAIN": "mock",
            "UTTERLANE_TTS_ENGINE": "none",
        }.items():
            os.environ.setdefault(key, value)


def cmd_run(args: argparse.Namespace) -> int:
    import uvicorn

    from utterlane_live.config import LiveConfig
    from utterlane_live.server import create_app

    cfg = LiveConfig.from_env()
    hardware = None
    if cfg.stt_mode != "mock":
        from utterlane_live.gpu import detect_hardware
        from utterlane_live.models import choose_stt, fetch, required_files

        hardware = detect_hardware()
        try:  # finishes an interrupted setup; a no-op when everything is present
            fetch(cfg.models_root, required_files(choose_stt(cfg, hardware)))
        except Exception as exc:
            print(f"Could not download the models: {type(exc).__name__}: {exc}", file=sys.stderr)
            print("Check the internet connection, then run `utterlane setup`.", file=sys.stderr)
            return 1
    url = f"http://{cfg.host}:{cfg.port}/"
    print(
        f"Utterlane Live → {url}  (loading models, this can take a moment)",
        file=sys.stderr,
        flush=True,
    )
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(
        create_app(cfg, hardware=hardware),
        host=cfg.host,
        port=cfg.port,
        log_level="warning",
        ws_max_size=8 * 1024 * 1024,
    )
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Download every model this machine needs and warm the voice once (online)."""

    from utterlane_live.config import LiveConfig
    from utterlane_live.gpu import detect_hardware
    from utterlane_live.models import choose_stt, fetch, required_files

    cfg = LiveConfig.from_env()
    hardware = detect_hardware()
    choice = choose_stt(cfg, hardware)
    print(f"Hardware: {hardware.describe()}")
    print(f"Speech-to-text: whisper-{choice.model.name} on {choice.device} ({choice.reason})")
    fetch(cfg.models_root, required_files(choice))
    if args.no_tts:
        return 0
    return _tts_check(cfg, online=True)


def _tts_check(cfg: object, *, online: bool) -> int:
    from utterlane_live.config import LiveConfig
    from utterlane_live.tts import WorkerTts
    from utterlane_live.voices import worker_spec

    assert isinstance(cfg, LiveConfig)
    spec = worker_spec(cfg, online=online)
    if spec is None:
        print("Voice output disabled (UTTERLANE_TTS_ENGINE=none).")
        return 0
    print(f"Preparing the voice ({cfg.language}, {spec.voice}) — the first run downloads it …")

    async def run() -> float:
        worker = WorkerTts(spec, cfg.data_dir / "tts-cache")
        try:
            await worker.start()
            audio = await worker.synthesize(spec.warmup_text)
            return len(audio) / worker.sample_rate
        finally:
            await worker.close()

    try:
        seconds = asyncio.run(run())
    except Exception as exc:
        print(f"Voice check failed: {type(exc).__name__}: {exc}")
        print(f"See the worker log in {cfg.data_dir / 'tts-pocket.log'}")
        return 1
    print(f"Voice ready ({seconds:.1f} s test sentence).")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from utterlane_live import __version__
    from utterlane_live.config import LiveConfig
    from utterlane_live.gpu import detect_hardware
    from utterlane_live.llm import display_url
    from utterlane_live.models import choose_stt, is_present, required_files
    from utterlane_live.profiles import default_key_provider

    cfg = LiveConfig.from_env()
    hardware = detect_hardware()
    choice = choose_stt(cfg, hardware)
    ok = True
    print(f"Utterlane Live {__version__} — Python {sys.version.split()[0]}")
    print(f"Hardware      : {hardware.describe()}")
    print(f"Language      : {cfg.language}")
    print(
        f"STT           : whisper-{choice.model.name} on {choice.device}/{choice.compute_type} ({choice.reason})"
    )
    missing = [
        m.relative_path for m in required_files(choice) if not is_present(cfg.models_root, m)
    ]
    print(
        f"Models        : {cfg.models_root} — {'all present' if not missing else f'{len(missing)} missing'}"
    )
    ok &= not missing
    brain = cfg.brain
    detail = display_url(brain.url) or (
        "OPENAI_API_KEY set" if brain.api_key else "no OPENAI_API_KEY"
    )
    print(f"Brain         : {brain.kind} ({brain.model or '-'}; {detail})")
    print(f"Voice         : {cfg.tts_engine} ({cfg.tts_voice})")
    print(f"Key storage   : {default_key_provider(cfg.data_dir).storage}")
    print(f"Data folder   : {cfg.data_dir}")
    web = cfg.static_dir / "index.html"
    print(f"Web UI        : {'present' if web.is_file() else 'MISSING (build the web folder)'}")
    ok &= web.is_file()
    if args.tts:
        ok &= _tts_check(cfg, online=False) == 0
    print("Result        :", "OK" if ok else "needs attention (run `utterlane setup`)")
    return 0 if ok else 1


def cmd_models(args: argparse.Namespace) -> int:
    from utterlane_live.config import LiveConfig
    from utterlane_live.gpu import detect_hardware
    from utterlane_live.models import (
        CORE_FILES,
        WHISPER,
        choose_stt,
        fetch,
        is_present,
        required_files,
    )

    cfg = LiveConfig.from_env()
    if args.action == "fetch":
        files = list(CORE_FILES)
        names = args.whisper or [choose_stt(cfg, detect_hardware()).model.name]
        for name in names:
            files += WHISPER[name].manifest()
        fetch(cfg.models_root, files)
        return 0
    choice = choose_stt(cfg, detect_hardware())
    missing = 0
    for item in required_files(choice):
        present = is_present(cfg.models_root, item, verify=args.action == "verify")
        missing += not present
        print(f"{'ok     ' if present else 'missing'} {item.relative_path}  [{item.license}]")
    return 1 if missing else 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from utterlane_live.mcp_server import run_stdio

    url = args.url or os.environ.get("UTTERLANE_URL") or "http://127.0.0.1:8765"
    run_stdio(url, os.environ.get("UTTERLANE_API_TOKEN"))
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    from utterlane_live.config import LiveConfig
    from utterlane_live.profiles import ProfileStore, default_key_provider

    cfg = LiveConfig.from_env()
    store = ProfileStore(
        cfg.data_dir / "voice-profiles.bin", default_key_provider(cfg.data_dir), ""
    )
    if args.action == "list":
        for item in store.list_public():
            print(f"{item['id']}  {item['name']}  samples={item['samples']}")
        return 0
    if args.action == "delete-all":
        print(f"deleted {store.delete_all()} profile(s)")
        return 0
    if args.action == "delete" and args.id:
        print("deleted" if store.delete(args.id) else "not found")
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    from utterlane_live.config import BRAIN_KINDS

    _utf8_console()
    parser = argparse.ArgumentParser(
        prog="utterlane", description="Utterlane Live — speaker-aware local voice front-end"
    )
    parser.add_argument(
        "--language", choices=["tr", "en"], help="conversation language (default: tr)"
    )
    parser.add_argument(
        "--voice", help="assistant voice (tr: female_1..3/male_1..3, en: alba, jean, marius, …)"
    )
    parser.add_argument("--brain", choices=BRAIN_KINDS, help="where replies come from")
    parser.add_argument("--stt-model", choices=["auto", "large-v3-turbo", "small"])
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start the app (default)")
    run.add_argument("--no-browser", action="store_true")
    run.add_argument("--mock", action="store_true", help="UI demo without models or network")

    setup = sub.add_parser("setup", help="download the models this machine needs")
    setup.add_argument("--no-tts", action="store_true")

    doctor = sub.add_parser("doctor", help="check the installation")
    doctor.add_argument("--tts", action="store_true", help="also start the voice once")

    models = sub.add_parser("models", help="model files")
    models.add_argument("action", choices=["status", "verify", "fetch"])
    models.add_argument("--whisper", nargs="*", choices=["large-v3-turbo", "small"])

    mcp = sub.add_parser("mcp", help="MCP server over stdio (talks to a running app)")
    mcp.add_argument("--url", help="app URL (default http://127.0.0.1:8765)")

    profiles = sub.add_parser("profiles", help="stored voice profiles")
    profiles.add_argument("action", choices=["list", "delete", "delete-all"])
    profiles.add_argument("id", nargs="?")

    args = parser.parse_args(argv)
    _apply_overrides(args)
    command = args.command or "run"
    if command == "run" and not hasattr(args, "no_browser"):
        args.no_browser = False
    handlers = {
        "run": cmd_run,
        "setup": cmd_setup,
        "doctor": cmd_doctor,
        "models": cmd_models,
        "mcp": cmd_mcp,
        "profiles": cmd_profiles,
    }
    return handlers[command](args)


if __name__ == "__main__":
    raise SystemExit(main())
