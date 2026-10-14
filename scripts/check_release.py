"""Pre-publish leak scan: fails if anything secret or private would be committed.

Checks every file git would track (``git ls-files --cached --others --exclude-standard``,
or a plain walk outside a git checkout) for:
- API keys and tokens (OpenAI, Anthropic, GitHub, Hugging Face, AWS, Slack, Google),
- private key blocks, ``.env`` files,
- personal paths (C:\\Users\\<name>, /home/<name>, /Users/<name>) and e-mail addresses,
- audio files, voice prints and model weights.

Usage: python scripts/check_release.py   (exit code 1 when something is found)
Extra forbidden strings can be listed one per line in ``.release-denylist`` (git-ignored).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SECRET_PATTERNS = {
    "openai_key": re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"),
    "anthropic_key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})"),
    "huggingface_token": re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY"),
    "assigned_secret": re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?(?!<|\$|\{|your|changeme|example|xxx|test|dummy|s3cret-token|k-123|w\b|gw-token)[A-Za-z0-9_\-]{16,}"
    ),
}
PERSONAL_PATTERNS = {
    "windows_user_path": re.compile(
        r"(?i)[A-Z]:\\+Users\\+(?!Public\b|Default\b|<|\{|%|\$)[^\\\s\"']+"
    ),
    "posix_home_path": re.compile(
        r"/(?:home|Users)/(?!runner\b|user\b|<|\{|\$|you\b|name\b)[a-z][a-z0-9_-]+/"
    ),
    "email": re.compile(
        r"\b[A-Za-z0-9._%+-]+@(?!example\.com|users\.noreply\.github\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ),
}
FORBIDDEN_SUFFIXES = {
    ".wav",
    ".mp3",
    ".m4a",
    ".flac",
    ".ogg",
    ".opus",
    ".npy",
    ".npz",
    ".onnx",
    ".bin",
    ".safetensors",
    ".pt",
    ".pth",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
}
FORBIDDEN_NAMES = {".env", "voice-profiles.bin", "profile.key", "llm-usage.json"}
ALLOWED_EMAILS = {"noreply@anthropic.com"}
TEXT_LIMIT = 2_000_000


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8")
        return [ROOT / p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        skip = {
            ".git",
            ".venv",
            ".tools",
            "node_modules",
            "__pycache__",
            ".mypy_cache",
            ".ruff_cache",
            ".pytest_cache",
        }
        return [
            p for p in ROOT.rglob("*") if p.is_file() and not skip & set(p.relative_to(ROOT).parts)
        ]


def main() -> int:
    denylist: list[str] = []
    extra = ROOT / ".release-denylist"
    if extra.is_file():
        denylist = [
            line.strip()
            for line in extra.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    findings: list[str] = []
    files = tracked_files()
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if rel == ".release-denylist":
            continue
        if path.name in FORBIDDEN_NAMES or (
            path.name.startswith(".env") and path.name != ".env.example"
        ):
            findings.append(f"{rel}: forbidden file")
            continue
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(
                f"{rel}: audio / voice print / weights / key file must not be committed"
            )
            continue
        if path.stat().st_size > TEXT_LIMIT and path.suffix.lower() not in {
            ".mp4",
            ".gif",
            ".png",
            ".jpg",
            ".webp",
        }:
            findings.append(f"{rel}: unexpectedly large file ({path.stat().st_size} bytes)")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{rel}:{lineno}: possible {name}")
            for name, pattern in PERSONAL_PATTERNS.items():
                for match in pattern.finditer(line):
                    if name == "email" and match.group(0).lower() in ALLOWED_EMAILS:
                        continue
                    findings.append(f"{rel}:{lineno}: {name}: {match.group(0)[:60]}")
            for word in denylist:
                if word.lower() in line.lower():
                    findings.append(f"{rel}:{lineno}: denylisted string")
    print(f"scanned {len(files)} files")
    if findings:
        print("\n".join(sorted(set(findings))))
        print(
            f"\nFAIL: {len(set(findings))} finding(s). Nothing should be published until they are fixed."
        )
        return 1
    print("OK: no secrets, personal paths, audio or weights found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
