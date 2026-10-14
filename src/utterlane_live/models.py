"""Model manifest, hardware-based model choice and hash-verified downloads.

Every file comes from its official source and is checked against a pinned SHA-256 before it
is used. Models live in ``<data dir>/models`` (``utterlane models status`` shows where).
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from utterlane_live.config import LiveConfig
from utterlane_live.gpu import Hardware

SHERPA_SPK = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models"


@dataclass(frozen=True, slots=True)
class ModelFile:
    group: str
    relative_path: str
    url: str
    sha256: str
    size: int
    license: str


@dataclass(frozen=True, slots=True)
class WhisperModel:
    name: str
    repo: str
    revision: str
    files: tuple[tuple[str, str, int], ...]  # (file name, sha256, size)
    license: str = "MIT (OpenAI Whisper; CTranslate2 conversion)"

    def folder(self, models_root: Path) -> Path:
        return models_root / "stt" / self.name

    def manifest(self) -> list[ModelFile]:
        return [
            ModelFile(
                "stt",
                f"stt/{self.name}/{file_name}",
                f"https://huggingface.co/{self.repo}/resolve/{self.revision}/{file_name}",
                sha,
                size,
                self.license,
            )
            for file_name, sha, size in self.files
        ]


WHISPER: dict[str, WhisperModel] = {
    "large-v3-turbo": WhisperModel(
        "large-v3-turbo",
        "dropbox-dash/faster-whisper-large-v3-turbo",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        (
            (
                "config.json",
                "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
                2_263,
            ),
            (
                "preprocessor_config.json",
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
                340,
            ),
            (
                "tokenizer.json",
                "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
                2_710_337,
            ),
            (
                "vocabulary.json",
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
                1_068_114,
            ),
            (
                "model.bin",
                "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
                1_617_884_929,
            ),
        ),
    ),
    "small": WhisperModel(
        "small",
        "Systran/faster-whisper-small",
        "536b0662742c02347bc0e980a01041f333bce120",
        (
            (
                "config.json",
                "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
                2_370,
            ),
            (
                "tokenizer.json",
                "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
                2_203_239,
            ),
            (
                "vocabulary.txt",
                "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
                459_861,
            ),
            (
                "model.bin",
                "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
                483_546_902,
            ),
        ),
    ),
}

CORE_FILES: tuple[ModelFile, ...] = (
    ModelFile(
        "vad",
        "vad/silero_vad_v6.2.1.onnx",
        "https://github.com/snakers4/silero-vad/raw/v6.2.1/src/silero_vad/data/silero_vad.onnx",
        "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
        2_327_524,
        "MIT (Silero VAD)",
    ),
    ModelFile(
        "speaker",
        "speaker/nemo_en_titanet_large.onnx",
        f"{SHERPA_SPK}/nemo_en_titanet_large.onnx",
        "d51abcf31717ef28162f26acb9d44dd4127c3d44c9b8624f699f3425daca8e77",
        101_405_493,
        "CC-BY-4.0 (NVIDIA NeMo TitaNet-L)",
    ),
    ModelFile(
        "speaker",
        "speaker/wespeaker_en_voxceleb_resnet34_LM.onnx",
        f"{SHERPA_SPK}/wespeaker_en_voxceleb_resnet34_LM.onnx",
        "e9848563da86f263117134dfd7ad63c92355b37de492b55e325400c9d9c39012",
        26_530_550,
        "CC-BY-4.0 (WeSpeaker ResNet34-LM, VoxCeleb)",
    ),
)

VAD_FILE = CORE_FILES[0].relative_path


@dataclass(frozen=True, slots=True)
class SttChoice:
    model: WhisperModel
    device: str
    compute_type: str
    beam_size: int
    reason: str


def choose_stt(cfg: LiveConfig, hardware: Hardware) -> SttChoice:
    """Pick the Whisper model and device for this machine (overridable from config)."""

    device = cfg.stt_device
    if device == "auto":
        device = "cuda" if hardware.cuda else "cpu"
    if device == "cuda" and not hardware.cuda:
        device = "cpu"
    name = cfg.stt_model.removeprefix("faster-whisper-")
    reason = "configured"
    if name == "auto":
        if device == "cuda":
            name, reason = "large-v3-turbo", "CUDA GPU"
        elif hardware.cpu_count >= 16:
            name, reason = "large-v3-turbo", f"CPU with {hardware.cpu_count} threads"
        else:
            name, reason = "small", f"CPU with {hardware.cpu_count} threads (faster model)"
    if name not in WHISPER:
        raise ValueError(f"unknown STT model {cfg.stt_model!r}; choose one of {sorted(WHISPER)}")
    compute = cfg.stt_compute_type
    if compute == "auto":
        compute = "int8_float16" if device == "cuda" else "int8"
    beam = cfg.stt_beam_size or (5 if device == "cuda" else 1)
    return SttChoice(WHISPER[name], device, compute, beam, reason)


def required_files(choice: SttChoice) -> list[ModelFile]:
    return [*CORE_FILES, *choice.model.manifest()]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_present(models_root: Path, item: ModelFile, *, verify: bool = False) -> bool:
    path = models_root / item.relative_path
    if not path.is_file() or path.stat().st_size != item.size:
        return False
    return not verify or sha256_of(path) == item.sha256


def download(models_root: Path, item: ModelFile, *, quiet: bool = False) -> None:
    target = models_root / item.relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False, suffix=".part") as tmp:
        tmp_path = Path(tmp.name)
        try:
            request = urllib.request.Request(item.url, headers={"User-Agent": "utterlane-live"})
            with urllib.request.urlopen(request, timeout=60) as response:
                done = 0
                while chunk := response.read(1 << 20):
                    tmp.write(chunk)
                    done += len(chunk)
                    if not quiet and item.size > 50_000_000:
                        print(
                            f"\r  {done / item.size:6.1%}  {item.relative_path}",
                            end="",
                            file=sys.stderr,
                        )
        except BaseException:  # no half-written .part files left behind
            tmp.close()
            tmp_path.unlink(missing_ok=True)
            raise
    if not quiet and item.size > 50_000_000:
        print(file=sys.stderr)
    digest = sha256_of(tmp_path)
    if digest != item.sha256:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"hash mismatch for {item.relative_path}: {digest}")
    os.replace(tmp_path, target)


def fetch(models_root: Path, files: list[ModelFile]) -> int:
    """Download missing files; returns how many were fetched."""

    missing = [m for m in files if not is_present(models_root, m)]
    if missing:
        total = sum(m.size for m in missing) / 1e9
        print(f"Downloading {len(missing)} model file(s), ~{total:.2f} GB → {models_root}")
    for item in missing:
        print(f"- {item.relative_path}  [{item.license}]")
        download(models_root, item)
    return len(missing)
