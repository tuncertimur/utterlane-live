"""Encrypted on-device storage for consented voice profiles.

File format: ``b"ULP1" + nonce(12) + AES-256-GCM(json)``. The key is created once and kept
in the OS credential store (Windows Credential Manager, macOS Keychain, Secret Service on
Linux) through ``keyring``. When no credential store is available a key file with
owner-only permissions is used instead and the API reports ``key_storage="file"``.

Voice vectors never leave this module except as in-memory arrays for the tracker; the
public listing exposes only names, timestamps and sample counts.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from utterlane_live.speakers import StoredProfile, Vector

MAGIC = b"ULP1"
KEYRING_SERVICE = "Utterlane"
KEYRING_USER = "voice-profile-key-v1"
MAX_STORED_VECTORS = 30


class KeyProvider(Protocol):
    storage: str

    def get_or_create(self) -> bytes: ...

    def delete(self) -> None: ...


class KeyringKeyProvider:
    storage = "os_credential_store"

    def get_or_create(self) -> bytes:
        import keyring

        encoded = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if encoded:
            return base64.b64decode(encoded)
        key = AESGCM.generate_key(bit_length=256)
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, base64.b64encode(key).decode("ascii"))
        return key

    def delete(self) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except PasswordDeleteError:
            pass


class FileKeyProvider:
    storage = "file"

    def __init__(self, path: Path) -> None:
        self.path = path

    def get_or_create(self) -> bytes:
        if self.path.is_file():
            return base64.b64decode(self.path.read_bytes())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = AESGCM.generate_key(bit_length=256)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(base64.b64encode(key))
        return key

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


def default_key_provider(data_dir: Path) -> KeyProvider:
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring

        if isinstance(keyring.get_keyring(), FailKeyring):
            raise RuntimeError("no_keyring_backend")
        return KeyringKeyProvider()
    except Exception:
        return FileKeyProvider(data_dir / "profile.key")


@dataclass(frozen=True, slots=True)
class LoadResult:
    profiles: list[StoredProfile]
    skipped_other_model: int = 0
    error: str | None = None


def _encode_vectors(vectors: list[Vector]) -> list[str]:
    return [base64.b64encode(np.asarray(v, dtype="<f4").tobytes()).decode("ascii") for v in vectors]


def _decode_vectors(items: list[str]) -> list[Vector]:
    return [np.frombuffer(base64.b64decode(item), dtype="<f4").astype(np.float32) for item in items]


class ProfileStore:
    def __init__(self, path: Path, key_provider: KeyProvider, model_fingerprint: str) -> None:
        self.path = path
        self.key_provider = key_provider
        self.model_fingerprint = model_fingerprint

    # ----------------------------------------------------------------- crypto
    def _encrypt(self, payload: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return (
            MAGIC + nonce + AESGCM(self.key_provider.get_or_create()).encrypt(nonce, payload, MAGIC)
        )

    def _decrypt(self, blob: bytes) -> bytes:
        if blob[:4] != MAGIC or len(blob) < 4 + 12 + 16:
            raise ValueError("profile_store_format_invalid")
        nonce, body = blob[4:16], blob[16:]
        return AESGCM(self.key_provider.get_or_create()).decrypt(nonce, body, MAGIC)

    # -------------------------------------------------------------------- io
    def _read_all(self) -> list[dict[str, object]]:
        if not self.path.is_file():
            return []
        data = json.loads(self._decrypt(self.path.read_bytes()).decode("utf-8"))
        if data.get("version") != 1:
            raise ValueError("profile_store_version_unsupported")
        return list(data.get("profiles", []))

    def _write_all(self, records: list[dict[str, object]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "profiles": records}, ensure_ascii=False).encode(
            "utf-8"
        )
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self._encrypt(payload))
        os.replace(tmp, self.path)

    def load(self) -> LoadResult:
        try:
            records = self._read_all()
        except Exception as exc:  # corrupted file or missing key: never crash the app
            return LoadResult([], error=type(exc).__name__)
        profiles: list[StoredProfile] = []
        skipped = 0
        for record in records:
            if record.get("model_fingerprint") != self.model_fingerprint:
                skipped += 1
                continue
            profiles.append(
                StoredProfile(
                    id=str(record["id"]),
                    name=str(record["name"]),
                    vectors=_decode_vectors(list(record["vectors"])),  # type: ignore[call-overload]
                    created_at=float(record["created_at"]),  # type: ignore[arg-type]
                    updated_at=float(record["updated_at"]),  # type: ignore[arg-type]
                    consent_method=str(record.get("consent_method", "spoken")),
                    model_fingerprint=self.model_fingerprint,
                )
            )
        return LoadResult(profiles, skipped)

    def upsert(self, profile: StoredProfile) -> None:
        records = [r for r in self._read_all() if r.get("id") != profile.id]
        records.append(
            {
                "id": profile.id,
                "name": profile.name,
                "vectors": _encode_vectors(profile.vectors[-MAX_STORED_VECTORS:]),
                "created_at": profile.created_at,
                "updated_at": profile.updated_at,
                "consent_method": profile.consent_method,
                "model_fingerprint": self.model_fingerprint,
            }
        )
        self._write_all(records)

    def create(
        self, name: str, vectors: list[Vector], consent_method: str = "spoken"
    ) -> StoredProfile:
        now = time.time()
        profile = StoredProfile(
            id=f"prof-{uuid.uuid4().hex[:12]}",
            name=name,
            vectors=list(vectors)[-MAX_STORED_VECTORS:],
            created_at=now,
            updated_at=now,
            consent_method=consent_method,
            model_fingerprint=self.model_fingerprint,
        )
        self.upsert(profile)
        return profile

    def delete(self, profile_id: str) -> bool:
        records = self._read_all()
        kept = [r for r in records if r.get("id") != profile_id]
        if len(kept) == len(records):
            return False
        self._write_all(kept)
        return True

    def delete_all(self) -> int:
        count = len(self._read_all()) if self.path.is_file() else 0
        self.path.unlink(missing_ok=True)
        return count

    def list_public(self) -> list[dict[str, object]]:
        try:
            records = self._read_all()
        except Exception:
            return []
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "samples": len(r.get("vectors", [])),  # type: ignore[arg-type]
                "compatible": r.get("model_fingerprint") == self.model_fingerprint,
            }
            for r in records
        ]
