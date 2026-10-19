"""Local TTS worker process (Pocket TTS), speaking JSON lines on stdin/stdout.

Run by the app as ``python -m utterlane_live.tts_worker --serve --language tr`` in the same
environment. Kept out of process so a TTS crash or a CUDA problem never takes the app down.

Engines
- ``tr``: Pocket TTS Turkish (wite-tech/pocket-tts-turkish, CC-BY-4.0) with its six bundled
  synthetic voices ``female_1..3`` / ``male_1..3``.
- ``en``/``de``/``fr``/``es``/``it``/``pt``/``nl``: Kyutai Pocket TTS (CC-BY-4.0) with its
  catalog voices (``alba``, ``jean``, ``marius``, …), loaded from the ungated
  ``kyutai/pocket-tts-without-voice-cloning`` weights. No voice cloning is used.

Protocol
    stdin : one JSON object per line: {"id", "text", "voice"?, "stream"?: bool}
            or {"command": "shutdown"}
    stdout: first line {"event": "ready", ...} (or {"event": "load_failed", "error"});
            then per request either
              {"id", "path", "sample_rate", "seconds", "synth_ms"}       (file mode) or
              {"id", "seq", "sample_rate", "pcm16": <base64>} ... then {"id", "done": true}
            or {"id", "error"}.
Library output (prints, progress bars) is redirected to stderr so stdout stays clean.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

TURKISH_REPO = "wite-tech/pocket-tts-turkish"
TURKISH_REVISION = "fcbd37c74dc127be846face0ff36062e9a939f7b"
POCKET_LANGUAGES = {
    "en": "english",
    "de": "german",
    "fr": "french",
    "es": "spanish",
    "it": "italian",
    "pt": "portuguese",
    "nl": "dutch",
}
DEFAULT_VOICES = {"tr": "female_1", "en": "alba"}


def pick_voice(language: str, wanted: str | None, available: list[str]) -> tuple[str, str]:
    """The voice to use and a note when the wanted one does not exist for this language."""

    default = DEFAULT_VOICES.get(language, available[0])
    if default not in available:
        default = available[0]
    if not wanted or wanted == "default":
        return default, ""
    if wanted in available:
        return wanted, ""
    return default, f"voice {wanted!r} is not available in {language!r}; using {default!r}"


def resolve_device(choice: str) -> str:
    import torch

    if choice == "cpu":
        return "cpu"
    if choice in ("auto", "cuda") and torch.cuda.is_available():
        return "cuda"
    return "cpu"


class PocketEngine:
    def __init__(self, language: str, voice: str | None, device: str) -> None:
        import torch

        self.language = language
        self.device = resolve_device(device)
        if self.device == "cpu":
            torch.set_num_threads(max(1, min(8, (os.cpu_count() or 4) - 1)))
        if language == "tr":
            from pocket_tts_turkish import TurkishTTS

            self.tts = TurkishTTS.from_pretrained(
                TURKISH_REPO, device=self.device, revision=TURKISH_REVISION
            )
            self.sample_rate = self.tts.sample_rate
            self.voices = list(self.tts.voices)
        elif language in POCKET_LANGUAGES:
            from pocket_tts import TTSModel
            from pocket_tts.utils.utils import _ORIGINS_OF_PREDEFINED_VOICES

            model = TTSModel.load_model(language=POCKET_LANGUAGES[language])
            self.model = model if self.device == "cpu" else model.to(self.device)
            self.sample_rate = int(self.model.sample_rate)
            self.voices = sorted(_ORIGINS_OF_PREDEFINED_VOICES)
            self._states: dict[str, Any] = {}
        else:
            raise ValueError(f"unsupported language {language!r}")
        self.default_voice, self.note = pick_voice(language, voice, self.voices)

    def _voice(self, voice: str | None) -> str:
        chosen = voice if voice and voice != "default" else self.default_voice
        return chosen if chosen in self.voices else self.default_voice

    def stream(self, text: str, voice: str | None) -> Iterator[np.ndarray]:
        name = self._voice(voice)
        if self.language == "tr":
            yield from self.tts.stream(text, voice=name)
            return
        state = self._states.get(name)
        if state is None:
            state = self._states[name] = self.model.get_state_for_audio_prompt(name)
        for chunk in self.model.generate_audio_stream(state, text):
            yield chunk.detach().cpu().numpy().reshape(-1).astype(np.float32)

    def synthesize(self, text: str, voice: str | None) -> np.ndarray:
        parts = list(self.stream(text, voice))
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)


def _emit(stream: Any, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def _write_wav(path: Path, audio: np.ndarray, rate: int) -> float:
    pcm = (np.clip(np.nan_to_num(audio), -1.0, 1.0) * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())
    return float(pcm.size) / float(rate)


def _serve_stream(engine: PocketEngine, request: dict[str, Any], out: Any) -> None:
    request_id, started = request["id"], time.perf_counter()
    rate, seq, total = engine.sample_rate, 0, 0
    pending: list[np.ndarray] = []
    pending_len = 0

    def flush() -> None:
        nonlocal pending, pending_len, seq, total
        if not pending:
            return
        block = np.concatenate(pending)
        pcm = (np.clip(block, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        _emit(
            out,
            {
                "id": request_id,
                "seq": seq,
                "sample_rate": rate,
                "pcm16": base64.b64encode(pcm).decode("ascii"),
            },
        )
        seq, total = seq + 1, total + len(block)
        pending, pending_len = [], 0

    for piece in engine.stream(request["text"], request.get("voice")):
        pending.append(np.asarray(piece, dtype=np.float32).reshape(-1))
        pending_len += pending[-1].shape[0]
        if pending_len >= rate * (0.25 if seq == 0 else 0.5):
            flush()
    flush()
    _emit(
        out,
        {
            "id": request_id,
            "done": True,
            "chunks": seq,
            "sample_rate": rate,
            "seconds": round(total / rate, 3),
            "synth_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Utterlane Live TTS worker")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=Path.cwd() / "tts-out")
    parser.add_argument("--language", default="tr")
    parser.add_argument("--voice", default=None)
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--text", help="single-shot: text to speak")
    parser.add_argument("--out", type=Path, help="single-shot: output WAV")
    args = parser.parse_args(argv)

    protocol = sys.stdout
    for stream in (sys.stdin, protocol, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            try:
                stream.reconfigure(encoding="utf-8")
            except ValueError:
                pass
    sys.stdout = sys.stderr  # keep library prints off the protocol stream

    started = time.perf_counter()
    try:
        engine = PocketEngine(args.language, args.voice, args.device)
        engine.synthesize("Merhaba." if args.language == "tr" else "Hello.", None)  # warm-up
    except Exception as error:
        _emit(protocol, {"event": "load_failed", "error": f"{type(error).__name__}: {error}"})
        return 3
    ready = {
        "event": "ready",
        "engine": "pocket",
        "language": args.language,
        "device": engine.device,
        "sample_rate": engine.sample_rate,
        "voices": engine.voices,
        "voice": engine.default_voice,
        "note": engine.note,
        "load_ms": round((time.perf_counter() - started) * 1000, 1),
    }

    if not args.serve:
        if not args.text or not args.out:
            print("--text and --out are required without --serve", file=sys.stderr)
            return 2
        seconds = _write_wav(args.out, engine.synthesize(args.text, args.voice), engine.sample_rate)
        _emit(protocol, {**ready, "path": str(args.out), "seconds": round(seconds, 3)})
        return 0

    _emit(protocol, ready)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            _emit(protocol, {"id": None, "error": f"JSONDecodeError: {error}"})
            continue
        if request.get("command") == "shutdown":
            break
        request_id = str(request.get("id") or f"utt-{int(time.time() * 1000)}")
        request["id"] = request_id
        text = str(request.get("text", "")).strip()
        if not text:
            _emit(protocol, {"id": request_id, "error": "ValueError: empty text"})
            continue
        request["text"] = text
        try:
            if request.get("stream"):
                _serve_stream(engine, request, protocol)
                continue
            synth_started = time.perf_counter()
            audio = engine.synthesize(text, request.get("voice"))
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", request_id)[:80] or "utt"
            path = (args.out_dir / f"{name}.wav").resolve()
            seconds = _write_wav(path, audio, engine.sample_rate)
            _emit(
                protocol,
                {
                    "id": request_id,
                    "path": str(path),
                    "sample_rate": engine.sample_rate,
                    "seconds": round(seconds, 3),
                    "synth_ms": round((time.perf_counter() - synth_started) * 1000, 1),
                },
            )
        except Exception as error:
            _emit(protocol, {"id": request_id, "error": f"{type(error).__name__}: {error}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
