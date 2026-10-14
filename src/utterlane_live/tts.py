"""Text-to-speech: sentence chunking, Turkish text normalization and local engines.

Heavy TTS models (Chatterbox, Freya, Pocket TTS) run in their own virtualenvs as worker
processes that speak a JSON-lines protocol on stdin/stdout:

    request : {"id": "...", "text": "...", "voice": "..."}
    response: {"id": "...", "path": "C:/.../x.wav", "sample_rate": 24000, "seconds": 1.2,
               "synth_ms": 480}   or   {"id": "...", "error": "..."}

The worker writes a WAV file; the backend reads and deletes it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import subprocess
import uuid
import wave
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

import numpy as np

from utterlane_live.audio import Float32


class TtsEngine(Protocol):
    engine_id: str
    sample_rate: int

    async def synthesize(self, text: str) -> Float32: ...

    async def close(self) -> None: ...


# --------------------------------------------------------------------------- text
_ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
_TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
_SCALES = [(10**12, "trilyon"), (10**9, "milyar"), (10**6, "milyon"), (1000, "bin")]


def number_to_turkish(n: int) -> str:
    if n == 0:
        return "sıfır"
    if n < 0:
        return "eksi " + number_to_turkish(-n)
    words: list[str] = []
    for value, name in _SCALES:
        if n >= value:
            count, n = divmod(n, value)
            if not (value == 1000 and count == 1):
                words.append(number_to_turkish(count))
            words.append(name)
    if n >= 100:
        hundreds, n = divmod(n, 100)
        if hundreds > 1:
            words.append(_ONES[hundreds])
        words.append("yüz")
    if n >= 10:
        words.append(_TENS[n // 10])
        n %= 10
    if n:
        words.append(_ONES[n])
    return " ".join(words)


def _replace_numbers(text: str) -> str:
    text = re.sub(r"\b(\d+)\s*[–—-]\s*(\d+)\b", r"\1 ile \2", text)
    text = re.sub(
        r"\b(\d{1,2})[:.](\d{2})\b(?!\.\d)",
        lambda m: (
            number_to_turkish(int(m.group(1)))
            + ("" if m.group(2) == "00" else " " + number_to_turkish(int(m.group(2))))
        ),
        text,
    )
    text = re.sub(r"%\s?(\d+)", lambda m: "yüzde " + number_to_turkish(int(m.group(1))), text)
    text = re.sub(
        r"\b(\d+),(\d+)\b",
        lambda m: (
            number_to_turkish(int(m.group(1))) + " virgül " + number_to_turkish(int(m.group(2)))
        ),
        text,
    )
    text = re.sub(
        r"\b(\d{1,3}(?:\.\d{3})+)\b",
        lambda m: number_to_turkish(int(m.group(1).replace(".", ""))),
        text,
    )
    text = re.sub(
        r"\b\d+\b",
        lambda m: number_to_turkish(int(m.group(0))) if len(m.group(0)) <= 13 else m.group(0),
        text,
    )
    return text


_EMOJI = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff]")


def normalize_for_speech(text: str) -> str:
    text = _EMOJI.sub("", text)
    text = re.sub(r"[*_#`>|~]+", " ", text)
    text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
    text = re.sub(r"https?://\S+", "bağlantı", text)
    text = _replace_numbers(text)
    text = text.replace("&", " ve ")
    return re.sub(r"\s+", " ", text).strip()


class SentenceChunker:
    """Splits a streamed reply into speakable chunks as soon as they are complete.

    The first chunk may end at a comma once it is long enough, so the first audio starts
    sooner; later chunks prefer whole sentences.
    """

    def __init__(
        self, min_chars: int = 12, soft_limit: int = 160, first_comma_after: int = 28
    ) -> None:
        self.buffer = ""
        self.min_chars = min_chars
        self.soft_limit = soft_limit
        self.first_comma_after = first_comma_after
        self.emitted = 0

    def feed(self, delta: str) -> Iterator[str]:
        self.buffer += delta
        while True:
            match = re.search(r"[.!?…](?:[\"'”’)]*)(?=\s)", self.buffer)
            first_comma = (
                re.search(r"[,;:](?=\s)", self.buffer[self.first_comma_after :])
                if self.emitted == 0
                else None
            )
            comma_cut = self.first_comma_after + first_comma.end() if first_comma else None
            if comma_cut is not None and (match is None or comma_cut < match.end()):
                cut = comma_cut
            elif match and match.end() >= self.min_chars:
                cut = match.end()
            elif len(self.buffer) > self.soft_limit:
                comma = max(
                    self.buffer.rfind(", ", 0, self.soft_limit),
                    self.buffer.rfind("; ", 0, self.soft_limit),
                )
                if comma < self.min_chars:
                    return
                cut = comma + 1
            else:
                return
            chunk, self.buffer = self.buffer[:cut].strip(), self.buffer[cut:]
            if chunk:
                self.emitted += 1
                yield chunk

    def flush(self) -> Iterator[str]:
        chunk, self.buffer = self.buffer.strip(), ""
        if chunk:
            yield chunk


# ------------------------------------------------------------------------ engines
class MockTts:
    """Soft two-tone 'speech' whose length follows the text; no model needed."""

    engine_id = "mock"
    sample_rate = 24_000

    async def synthesize(self, text: str) -> Float32:
        seconds = min(6.0, 0.25 + 0.055 * len(text))
        t = np.arange(int(seconds * self.sample_rate), dtype=np.float32) / self.sample_rate
        envelope = 0.5 - 0.5 * np.cos(2 * math.pi * np.minimum(t / seconds, 1.0))
        tone = 0.06 * np.sin(2 * math.pi * 220 * t) + 0.03 * np.sin(2 * math.pi * 330 * t)
        return (tone * envelope).astype(np.float32)

    async def close(self) -> None:
        return None


def read_wav_float(path: Path) -> tuple[Float32, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError("unsupported_wav_width")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data.astype(np.float32), rate


@dataclass
class WorkerSpec:
    engine_id: str
    command: tuple[str, ...]
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    voice: str = "default"
    startup_timeout_s: float = 240.0
    request_timeout_s: float = 60.0
    streaming: bool = False
    warmup_text: str = "Merhaba."


class WorkerTts:
    """Persistent TTS worker process speaking JSON lines (see module docstring)."""

    def __init__(self, spec: WorkerSpec, out_dir: Path) -> None:
        self.spec = spec
        self.engine_id = spec.engine_id
        self.sample_rate = 24_000
        self.out_dir = out_dir
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    def _log_handle(self) -> int | IO[bytes]:
        """Worker stderr goes to a small rotating log next to the audio cache."""

        log = self.out_dir.parent / f"tts-{self.engine_id}.log"
        try:
            if log.exists() and log.stat().st_size > 2_000_000:
                log.unlink()
            return log.open("ab")
        except OSError:
            return asyncio.subprocess.DEVNULL

    async def start(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENAI", "UTTERLANE_"))}
        from utterlane_live.gpu import child_path

        env["PATH"] = child_path()
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "HF_HUB_OFFLINE": "1"})
        env.update(self.spec.env or {})
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self._proc = await asyncio.create_subprocess_exec(
            *self.spec.command,
            "--serve",
            "--out-dir",
            str(self.out_dir),
            *self.spec.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=self._log_handle(),
            env=env,
            creationflags=flags,
            limit=1 << 20,
        )
        # Warm-up request doubles as a readiness check (model load happens here).
        await self._request(self.spec.warmup_text, timeout=self.spec.startup_timeout_s)

    async def _request(self, text: str, timeout: float) -> Float32:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdout is None or proc.returncode is not None:
            raise RuntimeError("tts_worker_not_running")
        request_id = uuid.uuid4().hex[:10]
        payload = json.dumps(
            {"id": request_id, "text": text, "voice": self.spec.voice}, ensure_ascii=False
        )
        proc.stdin.write((payload + "\n").encode("utf-8"))
        await proc.stdin.drain()
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if not line:
                raise RuntimeError("tts_worker_exited")
            try:
                reply = json.loads(line.decode("utf-8"))
            except ValueError:
                continue  # stray library output on stdout
            if reply.get("event") == "load_failed":
                raise RuntimeError(f"tts_worker_load_failed:{reply.get('error')}")
            if reply.get("id") != request_id:
                continue
            if "error" in reply:
                raise RuntimeError(f"tts_worker_error:{reply['error']}")
            path = Path(reply["path"])
            try:
                audio, rate = read_wav_float(path)
            finally:
                path.unlink(missing_ok=True)
            self.sample_rate = rate
            return audio

    async def stream(self, text: str) -> AsyncIterator[Float32]:
        """Yield audio blocks while the worker is still generating (streaming workers only)."""

        if not self.spec.streaming:
            yield await self.synthesize(text)
            return
        async with self._lock:
            proc = self._proc
            if proc is None or proc.returncode is not None:
                await self.close()
                await self.start()
                proc = self._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None
            request_id = uuid.uuid4().hex[:10]
            payload = {"id": request_id, "text": text, "voice": self.spec.voice, "stream": True}
            line_out = json.dumps(payload, ensure_ascii=False) + "\n"
            proc.stdin.write(line_out.encode("utf-8"))
            await proc.stdin.drain()
            while True:
                line = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=self.spec.request_timeout_s
                )
                if not line:
                    raise RuntimeError("tts_worker_exited")
                try:
                    reply = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                if reply.get("id") != request_id:
                    continue  # leftovers of a cancelled stream
                if "error" in reply:
                    raise RuntimeError(f"tts_worker_error:{reply['error']}")
                if reply.get("done"):
                    return
                self.sample_rate = int(reply.get("sample_rate", self.sample_rate))
                pcm = base64.b64decode(reply["pcm16"])
                yield np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0

    async def synthesize(self, text: str) -> Float32:
        async with self._lock:
            try:
                return await self._request(text, timeout=self.spec.request_timeout_s)
            except (TimeoutError, RuntimeError):
                await self.close()
                await self.start()
                return await self._request(text, timeout=self.spec.request_timeout_s)

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc and proc.returncode is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except (TimeoutError, ProcessLookupError):
                proc.kill()


def pcm16_bytes(audio: Float32) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def resample_to_16k(audio: Float32, rate: int) -> Float32:
    """Simple polyphase-free resampler for self-voice embeddings (quality is not critical)."""

    if rate == 16_000:
        return audio
    duration = len(audio) / rate
    target = np.linspace(0, len(audio) - 1, int(duration * 16_000), dtype=np.float64)
    return np.interp(target, np.arange(len(audio)), audio).astype(np.float32)
