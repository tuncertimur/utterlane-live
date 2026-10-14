from pathlib import Path

import numpy as np
import pytest

from utterlane_live.profiles import MAGIC, FileKeyProvider, ProfileStore


def vec(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=448).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    return ProfileStore(tmp_path / "profiles.bin", FileKeyProvider(tmp_path / "k.key"), "fp-1")


def test_roundtrip_is_encrypted(store: ProfileStore) -> None:
    created = store.create("Zümra", [vec(1), vec(2)])
    blob = store.path.read_bytes()
    assert blob.startswith(MAGIC)
    assert "Zümra".encode() not in blob and b"Z\\u00fcmra" not in blob
    loaded = store.load()
    assert loaded.error is None
    [profile] = loaded.profiles
    assert profile.id == created.id and profile.name == "Zümra"
    np.testing.assert_allclose(profile.vectors[0], vec(1))
    assert store.list_public()[0]["samples"] == 2
    assert "vectors" not in store.list_public()[0]


def test_other_model_profiles_are_skipped(tmp_path: Path, store: ProfileStore) -> None:
    store.create("Tuncer", [vec(3)])
    other = ProfileStore(store.path, store.key_provider, "fp-2")
    result = other.load()
    assert result.profiles == [] and result.skipped_other_model == 1
    assert other.list_public()[0]["compatible"] is False


def test_delete_one_and_all(store: ProfileStore) -> None:
    a = store.create("A", [vec(4)])
    store.create("B", [vec(5)])
    assert store.delete(a.id)
    assert not store.delete(a.id)
    assert [p.name for p in store.load().profiles] == ["B"]
    assert store.delete_all() == 1
    assert not store.path.exists()


def test_wrong_key_does_not_crash(tmp_path: Path, store: ProfileStore) -> None:
    store.create("A", [vec(6)])
    stranger = ProfileStore(store.path, FileKeyProvider(tmp_path / "other.key"), "fp-1")
    result = stranger.load()
    assert result.profiles == [] and result.error == "InvalidTag"


def test_vectors_are_capped(store: ProfileStore) -> None:
    store.create("A", [vec(i) for i in range(50)])
    assert len(store.load().profiles[0].vectors) == 30
