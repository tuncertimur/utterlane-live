"""Speaker voice vectors (local, CPU).

Production uses two sherpa-onnx speaker models and fuses them: each embedding is
L2-normalized, the pair is concatenated and divided by sqrt(2), so the cosine between two
fused vectors equals the mean of the per-model cosines.

Model choice (measured on consented Turkish recordings of two speakers): NeMo TitaNet-L and
WeSpeaker ResNet34-LM separate the voices with EER 0 on 1 s chunks. Note: the sherpa-onnx
export of 3D-Speaker CAM++ produces degenerate vectors for many input lengths (a
segment-pooling artefact; EER 0.43 on the same data) and should not be used.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from utterlane_live.audio import SAMPLE_RATE, Float32

MAX_EMBED_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class SpeakerModelSpec:
    name: str
    relative_path: str
    sha256: str
    license: str


FUSED_MODELS = (
    SpeakerModelSpec(
        "nemo_titanet_large",
        "speaker/nemo_en_titanet_large.onnx",
        "d51abcf31717ef28162f26acb9d44dd4127c3d44c9b8624f699f3425daca8e77",
        "CC-BY-4.0 (NVIDIA NeMo TitaNet-L)",
    ),
    SpeakerModelSpec(
        "wespeaker_resnet34_lm",
        "speaker/wespeaker_en_voxceleb_resnet34_LM.onnx",
        "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012",
        "CC-BY-4.0 (WeSpeaker, VoxCeleb)",
    ),
)


class Embedder(Protocol):
    fingerprint: str
    dimension: int

    def embed(self, audio: Float32) -> Float32: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class SherpaFusedEmbedder:
    def __init__(
        self, models_dir: Path, specs: tuple[SpeakerModelSpec, ...] = FUSED_MODELS, threads: int = 2
    ) -> None:
        import sherpa_onnx  # type: ignore[import-untyped]

        self._extractors = []
        parts = []
        dimension = 0
        for spec in specs:
            path = models_dir / spec.relative_path
            if not path.is_file():
                raise RuntimeError(f"speaker_model_missing:{spec.name}")
            digest = _sha256(path)
            if spec.sha256 and digest != spec.sha256:
                raise RuntimeError(f"speaker_model_hash_mismatch:{spec.name}")
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(path), num_threads=threads, provider="cpu"
            )
            extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
            self._extractors.append(extractor)
            dimension += int(extractor.dim)
            parts.append(f"{spec.name}:{digest[:16]}")
        self.dimension = dimension
        self.fingerprint = "+".join(parts)

    def embed_fast(self, audio: Float32) -> Float32:
        """Single-model vector (ResNet34-LM, ~3x faster) for sliding-window change search."""

        extractor = self._extractors[-1]
        stream = extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, audio.astype(np.float32).tolist())
        stream.input_finished()
        vec = np.asarray(extractor.compute(stream), dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if not math.isfinite(norm) or norm <= 0:
            raise RuntimeError("speaker_vector_invalid")
        return (vec / norm).astype(np.float32)

    def embed(self, audio: Float32) -> Float32:
        if len(audio) > MAX_EMBED_SECONDS * SAMPLE_RATE:
            # Use the middle part: starts and ends carry pre-roll silence and breaths.
            excess = len(audio) - int(MAX_EMBED_SECONDS * SAMPLE_RATE)
            audio = audio[excess // 2 : excess // 2 + int(MAX_EMBED_SECONDS * SAMPLE_RATE)]
        samples = audio.astype(np.float32).tolist()
        vectors = []
        for extractor in self._extractors:
            stream = extractor.create_stream()
            stream.accept_waveform(SAMPLE_RATE, samples)
            stream.input_finished()
            vec = np.asarray(extractor.compute(stream), dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            if not math.isfinite(norm) or norm <= 0:
                raise RuntimeError("speaker_vector_invalid")
            vectors.append(vec / norm)
        fused = np.concatenate(vectors) / math.sqrt(len(vectors))
        return fused.astype(np.float32)


class MockEmbedder:
    """Maps audio to a voice vector keyed by its peak amplitude (in 1/100 steps).

    Tests encode each synthetic "speaker" as a distinct amplitude of the audio they push.
    """

    fingerprint = "mock-embedder-v1"
    dimension = 64

    def __init__(self) -> None:
        self._bases: dict[int, Float32] = {}

    def embed(self, audio: Float32) -> Float32:
        key = int(round(float(np.max(np.abs(audio), initial=0.0)) * 100))
        if key not in self._bases:
            rng = np.random.default_rng(key + 17)
            base = rng.normal(size=self.dimension).astype(np.float32)
            self._bases[key] = base / np.linalg.norm(base)
        return self._bases[key]

    def embed_fast(self, audio: Float32) -> Float32:
        return self.embed(audio)
