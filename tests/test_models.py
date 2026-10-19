from pathlib import Path

import pytest

from utterlane_live.config import LiveConfig
from utterlane_live.gpu import Hardware
from utterlane_live.models import WHISPER, choose_stt, is_present, required_files


def hw(cuda: bool, cpus: int, system: str = "Windows", machine: str = "amd64") -> Hardware:
    return Hardware(
        cuda=cuda, gpu_name="GPU" if cuda else None, cpu_count=cpus, system=system, machine=machine
    )


@pytest.mark.parametrize(
    ("hardware", "model", "device", "compute", "beam"),
    [
        (hw(True, 8), "large-v3-turbo", "cuda", "int8_float16", 5),
        (hw(False, 20), "large-v3-turbo", "cpu", "int8", 1),
        (hw(False, 8), "small", "cpu", "int8", 1),
        (hw(False, 10, "Darwin", "arm64"), "small", "cpu", "int8", 1),
    ],
)
def test_auto_choice(hardware: Hardware, model: str, device: str, compute: str, beam: int) -> None:
    choice = choose_stt(LiveConfig(), hardware)
    assert (choice.model.name, choice.device, choice.compute_type, choice.beam_size) == (
        model,
        device,
        compute,
        beam,
    )


def test_overrides_and_fallbacks() -> None:
    cfg = LiveConfig()
    cfg.stt_model = "faster-whisper-small"
    cfg.stt_device = "cuda"
    choice = choose_stt(cfg, hw(False, 8))
    assert (choice.model.name, choice.device) == ("small", "cpu")  # no CUDA → CPU
    cfg.stt_model = "medium"
    with pytest.raises(ValueError):
        choose_stt(cfg, hw(False, 8))


def test_manifest_is_pinned_and_complete(tmp_path: Path) -> None:
    files = required_files(choose_stt(LiveConfig(), hw(True, 8)))
    paths = [f.relative_path for f in files]
    assert "vad/silero_vad_v6.2.1.onnx" in paths
    assert any(p.endswith("model.bin") for p in paths)
    for item in files:
        assert len(item.sha256) == 64 and item.size > 0
        assert item.url.startswith("https://")
        assert not is_present(tmp_path, item)
    assert set(WHISPER) == {"large-v3-turbo", "small"}


class _Broken:
    def __init__(self, payload: bytes, fail: bool) -> None:
        self.payload, self.fail, self.sent = payload, fail, False

    def __enter__(self) -> "_Broken":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, _size: int) -> bytes:
        if self.sent:
            if self.fail:
                raise ConnectionResetError("connection dropped")
            return b""
        self.sent = True
        return self.payload


def test_download_verifies_hash_and_leaves_no_partial_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    from utterlane_live import models

    payload = b"model-bytes" * 100
    item = models.ModelFile(
        "test",
        "x/model.bin",
        "https://example.invalid/m",
        hashlib.sha256(payload).hexdigest(),
        len(payload),
        "MIT",
    )
    monkeypatch.setattr(models.urllib.request, "urlopen", lambda *a, **k: _Broken(payload, True))
    with pytest.raises(ConnectionResetError):
        models.download(tmp_path, item, quiet=True)
    assert list((tmp_path / "x").iterdir()) == []

    bad = models.ModelFile("test", "x/model.bin", item.url, "0" * 64, len(payload), "MIT")
    monkeypatch.setattr(models.urllib.request, "urlopen", lambda *a, **k: _Broken(payload, False))
    with pytest.raises(RuntimeError, match="hash mismatch"):
        models.download(tmp_path, bad, quiet=True)
    assert list((tmp_path / "x").iterdir()) == []

    models.download(tmp_path, item, quiet=True)
    assert models.is_present(tmp_path, item, verify=True)
    assert [p.name for p in (tmp_path / "x").iterdir()] == ["model.bin"]


def test_gpu_without_cuda_libraries_plans_for_the_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    from utterlane_live import gpu

    fake = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    monkeypatch.setattr(gpu.sys, "platform", "linux")
    monkeypatch.setattr(gpu, "enable_cuda_libraries", lambda: [])
    monkeypatch.setattr(gpu, "_cuda_runtime_loadable", lambda: False)
    hardware = gpu.detect_hardware()
    assert not hardware.cuda and "without CUDA libraries" in hardware.describe()
    choice = choose_stt(LiveConfig(), hardware)
    assert choice.device == "cpu" and choice.compute_type == "int8"

    monkeypatch.setattr(gpu, "_cuda_runtime_loadable", lambda: True)
    monkeypatch.setattr(gpu, "_gpu_name", lambda: "Test GPU")
    assert gpu.detect_hardware().cuda


def test_voice_follows_the_language() -> None:
    from utterlane_live.tts_worker import pick_voice
    from utterlane_live.voices import worker_spec

    english = ["alba", "george", "jean"]
    assert pick_voice("en", None, english) == ("alba", "")
    assert pick_voice("en", "george", english) == ("george", "")
    voice, note = pick_voice("en", "female_1", english)
    assert voice == "alba" and "female_1" in note

    cfg = LiveConfig()
    cfg.language, cfg.tts_voice = "en", "female_1"  # a Turkish voice left in .env
    spec = worker_spec(cfg)
    assert spec is not None and spec.voice == "alba"
    cfg.language, cfg.tts_voice = "tr", "george"
    spec = worker_spec(cfg)
    assert spec is not None and spec.voice == "female_1"
    cfg.language, cfg.tts_voice = "tr", "male_2"
    spec = worker_spec(cfg)
    assert spec is not None and spec.voice == "male_2"
