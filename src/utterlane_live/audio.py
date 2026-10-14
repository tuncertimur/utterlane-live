"""Streaming voice-activity endpointing for 16 kHz mono PCM16 input.

``Endpointer`` turns a stream of 20 ms frames into utterances. Speech probability comes
from a pluggable VAD model (Silero v6 ONNX in production, an energy model in tests).
"""

from __future__ import annotations

import hashlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
from numpy.typing import NDArray

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 320
WINDOW_SAMPLES = 512

SILERO_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"

Float32 = NDArray[np.float32]


class VadModel(Protocol):
    def probability(self, window: Float32) -> float: ...

    def reset(self) -> None: ...


class SileroVad:
    """Silero VAD v6 ONNX; one instance per audio stream (it is stateful)."""

    def __init__(self, model_path: Path, *, verify: bool = True) -> None:
        import onnxruntime as ort  # type: ignore[import-untyped]

        if verify:
            digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
            if digest != SILERO_SHA256:
                raise RuntimeError("silero_model_hash_mismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, window: Float32) -> float:
        model_input = np.concatenate((self._context, window.reshape(1, -1)), axis=1)
        output, state = self._session.run(
            ["output", "stateN"],
            {
                "input": model_input,
                "state": self._state,
                "sr": np.asarray(SAMPLE_RATE, dtype=np.int64),
            },
        )
        self._state = np.asarray(state, dtype=np.float32)
        self._context = model_input[:, -64:]
        return float(np.asarray(output)[0, 0])


class EnergyVad:
    """Deterministic energy VAD for tests and the mock runtime."""

    def __init__(self, threshold: float = 0.02) -> None:
        self.threshold = threshold

    def reset(self) -> None:
        pass

    def probability(self, window: Float32) -> float:
        rms = float(np.sqrt(np.mean(np.square(window))))
        return 1.0 if rms >= self.threshold else 0.0


@dataclass(frozen=True, slots=True)
class EndpointerConfig:
    start_probability: float = 0.5
    end_probability: float = 0.35
    start_windows: int = 2
    end_silence_ms: int = 650
    preroll_ms: int = 300
    min_speech_ms: int = 300
    max_utterance_s: float = 18.0
    keep_trailing_ms: int = 200


@dataclass(slots=True)
class Utterance:
    audio: Float32
    started_at: float
    ended_at: float
    speech_ms: int
    forced_cut: bool = False

    @property
    def duration_s(self) -> float:
        return len(self.audio) / SAMPLE_RATE


@dataclass(frozen=True, slots=True)
class EndpointEvent:
    kind: Literal["speech_start", "speech_cancel", "utterance"]
    at: float
    utterance: Utterance | None = None


@dataclass
class Endpointer:
    vad: VadModel
    config: EndpointerConfig = field(default_factory=EndpointerConfig)
    clock: Callable[[], float] | None = None

    def __post_init__(self) -> None:
        self._preroll: deque[Float32] = deque(maxlen=max(1, self.config.preroll_ms // 20))
        self._pending = np.empty(0, dtype=np.float32)
        self._frames: list[Float32] = []
        self._active = False
        self._start_hits = 0
        self._silence_ms = 0
        self._speech_ms = 0
        self._started_at = 0.0
        self._t = 0.0
        self.last_probability = 0.0
        self.level = 0.0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def current_speech_ms(self) -> int:
        return self._speech_ms if self._active else 0

    def current_audio(self) -> Float32:
        return np.concatenate(self._frames) if self._frames else np.empty(0, dtype=np.float32)

    def reset(self) -> None:
        self.vad.reset()
        self.__post_init__()

    def push(self, pcm16: bytes) -> list[EndpointEvent]:
        samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        events: list[EndpointEvent] = []
        for start in range(0, len(samples), FRAME_SAMPLES):
            events.extend(self._push_frame(samples[start : start + FRAME_SAMPLES]))
        return events

    def _now(self) -> float:
        return self.clock() if self.clock else self._t

    def _push_frame(self, frame: Float32) -> list[EndpointEvent]:
        events: list[EndpointEvent] = []
        self._t += len(frame) / SAMPLE_RATE
        self.level = float(np.sqrt(np.mean(np.square(frame)))) if len(frame) else 0.0
        if self._active:
            self._frames.append(frame)
        else:
            self._preroll.append(frame)
        self._pending = np.concatenate((self._pending, frame))
        while len(self._pending) >= WINDOW_SAMPLES:
            window = self._pending[:WINDOW_SAMPLES]
            self._pending = self._pending[WINDOW_SAMPLES:]
            p = self.vad.probability(window)
            self.last_probability = p
            events.extend(self._step(p, WINDOW_SAMPLES * 1000 // SAMPLE_RATE))
        return events

    def _step(self, p: float, window_ms: int) -> list[EndpointEvent]:
        cfg = self.config
        now = self._now()
        if not self._active:
            self._start_hits = self._start_hits + 1 if p >= cfg.start_probability else 0
            if self._start_hits >= cfg.start_windows:
                self._active = True
                self._frames = list(self._preroll)
                self._preroll.clear()
                self._silence_ms = 0
                self._speech_ms = window_ms * self._start_hits
                self._started_at = now - len(self.current_audio()) / SAMPLE_RATE
                return [EndpointEvent("speech_start", now)]
            return []

        if p < cfg.end_probability:
            self._silence_ms += window_ms
        else:
            self._silence_ms = 0
            self._speech_ms += window_ms
        audio_s = sum(len(f) for f in self._frames) / SAMPLE_RATE
        if self._silence_ms >= cfg.end_silence_ms:
            return [self._finish(now, forced=False)]
        if audio_s >= cfg.max_utterance_s:
            event = self._finish(now, forced=True)
            # Keep listening: the same speaker is probably still talking.
            self._active = True
            self._frames = []
            self._speech_ms = 0
            self._silence_ms = 0
            self._started_at = now
            return [event]
        return []

    def _finish(self, now: float, *, forced: bool) -> EndpointEvent:
        cfg = self.config
        audio = self.current_audio()
        if not forced:
            trailing = max(0, self._silence_ms - cfg.keep_trailing_ms)
            cut = int(trailing * SAMPLE_RATE / 1000)
            if 0 < cut < len(audio):
                audio = audio[:-cut]
        speech_ms = self._speech_ms
        started_at = self._started_at
        self._active = False
        self._frames = []
        self._start_hits = 0
        self._silence_ms = 0
        self._speech_ms = 0
        if speech_ms < cfg.min_speech_ms:
            return EndpointEvent("speech_cancel", now)
        return EndpointEvent(
            "utterance",
            now,
            Utterance(
                audio=audio,
                started_at=started_at,
                ended_at=now,
                speech_ms=speech_ms,
                forced_cut=forced,
            ),
        )


def float_to_pcm16(audio: Float32) -> bytes:
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()
