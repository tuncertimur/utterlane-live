"""Speech-to-text engines: local Whisper on the GPU, plus a scripted engine for tests."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from utterlane_live.audio import Float32
from utterlane_live.models import SttChoice

# Phrases Whisper tends to invent on silence/noise in Turkish (subtitle credits etc.).
_HALLUCINATIONS = [
    r"altyaz[ıi]",
    r"izledi[ğg]iniz i[çc]in te[şs]ekk[üu]r",
    r"abone ol",
    r"videoyu be[ğg]en",
    r"bir sonraki videoda",
    r"m\.?\s?k\.?$",
    r"^\W*te[şs]ekk[üu]rler\W*$",
    r"^\W*(?:m+|h+m+|ı+h+)\W*$",
]
_HALLUCINATION_RE = re.compile("|".join(_HALLUCINATIONS), re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SttResult:
    text: str
    elapsed_ms: int
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    rejected: str | None = None
    words: tuple[tuple[float, float, str], ...] = ()


class SttEngine(Protocol):
    model_id: str

    def transcribe(self, audio: Float32, hotwords: tuple[str, ...] = ()) -> SttResult: ...


# Whisper with language="tr" occasionally emits a word in another script (e.g. Hangul).
_FOREIGN_SCRIPT = re.compile(
    "[\u0370-\u03ff\u0400-\u04ff\u0590-\u05ff\u0600-\u06ff\u0e00-\u0e7f"
    "\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u3400-\u9fff\uac00-\ud7af]+"
)


def clean_transcript(text: str) -> str:
    text = _FOREIGN_SCRIPT.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text.strip("-– ").strip()


class WhisperStt:
    def __init__(
        self,
        model_path: Path,
        *,
        device: str = "cuda",
        compute_type: str = "int8_float16",
        beam_size: int = 5,
        language: str = "tr",
        cpu_threads: int = 0,
    ) -> None:
        if device == "cuda":
            from utterlane_live.gpu import enable_cuda_libraries

            enable_cuda_libraries()
        from faster_whisper import WhisperModel  # type: ignore[import-untyped]

        self.model_id = f"whisper-{model_path.name} ({device})"
        self._model = WhisperModel(
            str(model_path), device=device, compute_type=compute_type, cpu_threads=cpu_threads
        )
        self.device = device
        self.beam_size = beam_size
        self.language = language

    def warmup(self) -> None:
        silence = np.zeros(16000, dtype=np.float32)
        self.transcribe(silence)

    def transcribe(self, audio: Float32, hotwords: tuple[str, ...] = ()) -> SttResult:
        started = time.perf_counter()
        segments, _info = self._model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            vad_filter=False,
            condition_on_previous_text=False,
            without_timestamps=False,
            word_timestamps=True,
            hotwords=" ".join(hotwords) if hotwords else None,
            temperature=(0.0, 0.2, 0.4),
            compression_ratio_threshold=2.2,
            log_prob_threshold=-1.0,
            no_speech_threshold=0.6,
        )
        parts: list[str] = []
        logprobs: list[float] = []
        no_speech: list[float] = []
        words: list[tuple[float, float, str]] = []
        for segment in segments:
            parts.append(segment.text)
            logprobs.append(segment.avg_logprob)
            no_speech.append(segment.no_speech_prob)
            words.extend((w.start, w.end, w.word) for w in segment.words or ())
        elapsed = int((time.perf_counter() - started) * 1000)
        text = clean_transcript(" ".join(parts))
        avg_lp = float(np.mean(logprobs)) if logprobs else None
        max_ns = float(np.max(no_speech)) if no_speech else None
        rejected = None
        if not re.search(r"\w", text):
            rejected = "empty"
        elif _HALLUCINATION_RE.search(text):
            rejected = "hallucination_pattern"
        elif max_ns is not None and avg_lp is not None and max_ns > 0.6 and avg_lp < -0.8:
            rejected = "no_speech"
        return SttResult(text, elapsed, avg_lp, max_ns, rejected, tuple(words))


class ScriptedStt:
    """Returns queued transcripts in order; used by tests and the mock runtime."""

    model_id = "scripted"

    def __init__(self, transcripts: list[str] | None = None) -> None:
        self.queue: deque[str] = deque(transcripts or [])

    def push(self, text: str) -> None:
        self.queue.append(text)

    def transcribe(self, audio: Float32, hotwords: tuple[str, ...] = ()) -> SttResult:
        text = self.queue.popleft() if self.queue else ""
        # Spread words evenly over the voiced part of the audio (tests use tones + silence).
        voiced = np.flatnonzero(np.abs(audio) > 0.01)
        tokens = text.split(" ") if text else []
        words: list[tuple[float, float, str]] = []
        if tokens and voiced.size:
            first, last = voiced[0] / 16000, voiced[-1] / 16000
            step = (last - first) / len(tokens)
            words = [
                (first + i * step, first + (i + 1) * step, (" " if i else "") + token)
                for i, token in enumerate(tokens)
            ]
        return SttResult(text, 1, rejected=None if text else "empty", words=tuple(words))


def load_whisper(
    choice: SttChoice, models_root: Path, *, language: str, cpu_threads: int = 0
) -> tuple[WhisperStt, SttChoice]:
    """Load the chosen Whisper model; if CUDA fails, fall back to the same model on the CPU."""

    try:
        stt = WhisperStt(
            choice.model.folder(models_root),
            device=choice.device,
            compute_type=choice.compute_type,
            beam_size=choice.beam_size,
            language=language,
            cpu_threads=cpu_threads,
        )
        stt.warmup()
        return stt, choice
    except Exception as exc:
        if choice.device != "cuda":
            raise
        fallback = SttChoice(
            choice.model, "cpu", "int8", 1, f"CUDA failed ({type(exc).__name__}); CPU fallback"
        )
        stt = WhisperStt(
            choice.model.folder(models_root),
            device="cpu",
            compute_type="int8",
            beam_size=1,
            language=language,
            cpu_threads=cpu_threads,
        )
        stt.warmup()
        return stt, fallback
