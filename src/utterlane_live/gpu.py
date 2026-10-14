"""Hardware detection and CUDA runtime loading for faster-whisper (CTranslate2).

CTranslate2's CUDA build needs cuBLAS 12 and cuDNN 9 at runtime. With the optional
``cuda`` extra they come from the ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` wheels:

- Windows: their ``bin`` folders are added to the DLL search path.
- Linux: their ``lib`` folders are preloaded with ``ctypes`` (RTLD_GLOBAL).
- macOS: no CUDA; everything runs on the CPU.

If anything fails, callers fall back to the CPU, so the app always starts.
"""

from __future__ import annotations

import ctypes
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

_DONE = False
# PATH entries added for CTranslate2. Child processes with their own CUDA runtime (the TTS
# worker's torch) must not inherit them, or they may load a mismatching cuDNN.
ADDED_PATHS: list[str] = []


def _nvidia_roots() -> list[Path]:
    try:
        import nvidia  # type: ignore[import-untyped]
    except ImportError:
        return []
    return [Path(p) for p in getattr(nvidia, "__path__", [])]


def enable_cuda_libraries() -> list[str]:
    """Make pip-installed CUDA 12 libraries loadable. Safe to call repeatedly."""

    global _DONE
    added: list[str] = []
    if _DONE:
        return added
    _DONE = True
    roots = _nvidia_roots()
    if sys.platform == "win32":
        for root in roots:
            for sub in sorted(root.iterdir()):
                bin_dir = sub / "bin"
                if bin_dir.is_dir():
                    os.add_dll_directory(str(bin_dir))
                    os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
                    added.append(str(bin_dir))
                    ADDED_PATHS.append(str(bin_dir))
    elif sys.platform.startswith("linux"):
        # Order matters: cublasLt before cublas, cudnn sub-libraries after libcudnn.
        patterns = [
            "cublas/lib/libcublasLt.so.*",
            "cublas/lib/libcublas.so.*",
            "cudnn/lib/libcudnn*.so.*",
        ]
        for root in roots:
            for pattern in patterns:
                for lib in sorted(root.glob(pattern)):
                    try:
                        ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
                        added.append(str(lib))
                    except OSError:
                        pass
    return added


def child_path() -> str:
    """PATH for child processes without the CUDA directories added above."""

    drop = {os.path.normcase(p) for p in ADDED_PATHS}
    parts = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(p for p in parts if os.path.normcase(p) not in drop)


@dataclass(frozen=True, slots=True)
class Hardware:
    cuda: bool
    gpu_name: str | None
    cpu_count: int
    system: str
    machine: str
    note: str = ""

    @property
    def apple_silicon(self) -> bool:
        return self.system == "Darwin" and self.machine in ("arm64", "aarch64")

    def describe(self) -> str:
        gpu = f"CUDA GPU ({self.gpu_name})" if self.cuda else (self.note or "no CUDA GPU")
        return f"{self.system} {self.machine}, {self.cpu_count} CPU threads, {gpu}"


def _cuda_runtime_loadable() -> bool:
    """cuBLAS 12 and cuDNN 9, which CTranslate2 needs, can be loaded (pip wheels or system)."""

    names = (
        ("cublas64_12.dll", "cudnn64_9.dll")
        if sys.platform == "win32"
        else ("libcublas.so.12", "libcudnn.so.9")
    )
    for name in names:
        try:
            if sys.platform == "win32":
                ctypes.WinDLL(name, winmode=0)  # standard search order, PATH included
            else:
                ctypes.CDLL(name)
        except OSError:
            return False
    return True


def detect_hardware() -> Hardware:
    cuda = False
    name: str | None = None
    note = ""
    if sys.platform != "darwin":
        try:
            enable_cuda_libraries()
            import ctranslate2  # type: ignore[import-untyped]

            cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            cuda = False
        if cuda and not _cuda_runtime_loadable():
            # A GPU and its driver, but a CPU-only install: plan for the CPU from the start.
            cuda = False
            note = "NVIDIA GPU without CUDA libraries (CPU install)"
        if cuda:
            name = _gpu_name()
    return Hardware(
        cuda=cuda,
        gpu_name=name,
        cpu_count=os.cpu_count() or 4,
        system=platform.system(),
        machine=platform.machine().lower(),
        note=note,
    )


def _gpu_name() -> str | None:
    try:
        import subprocess

        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=flags,
        )
        lines = out.stdout.strip().splitlines()
        return lines[0] if lines else "CUDA GPU"
    except Exception:
        return "CUDA GPU"
