from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from twin.bots.queue import acquire_lease, beat, clear_beat, heartbeat_key, release_lease
from twin.storage.profiles import pack_profile, profile_key, seal, unpack_profile, unseal


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(
        self, name: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    async def get(self, name: str) -> str | None:
        return self.store.get(name)

    async def delete(self, name: str) -> None:
        self.store.pop(name, None)


@pytest.mark.parametrize(
    ("excluded", "is_dir"),
    [
        ("SingletonLock", False),
        ("Cache/nested.bin", True),
        ("debug.log", False),
        ("LOCK", False),
    ],
    ids=["singleton", "cache-dir", "log-file", "lock-file"],
)
def test_pack_skips_ephemeral_browser_files(tmp_path: Path, excluded: str, is_dir: bool) -> None:
    source = tmp_path / "profile"
    (source / "Default").mkdir(parents=True)
    (source / "Default" / "kept.txt").write_text("keep")
    target = source / excluded
    if is_dir:
        target.mkdir(parents=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("drop")
    dest = tmp_path / "restored"
    unpack_profile(pack_profile(source), dest)
    assert (dest / "Default" / "kept.txt").read_text() == "keep"
    assert not (dest / excluded).exists()


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        ("guest", "profiles/default/meet/guest/profile.tar.gz"),
        ("signed", "profiles/default/meet/signed/profile.tar.gz"),
    ],
    ids=["guest-tier", "signed-tier"],
)
def test_profile_key_layout(tier: str, expected: str) -> None:
    assert profile_key("default", "meet", tier) == expected


def test_seal_roundtrips_with_key() -> None:
    key = Fernet.generate_key().decode()
    assert unseal(seal(b"profile-bytes", key), key) == b"profile-bytes"


def test_seal_without_key_passes_through() -> None:
    assert seal(b"profile-bytes", None) == b"profile-bytes"
    assert unseal(b"profile-bytes", None) == b"profile-bytes"


def test_seal_with_bad_key_fails_loudly() -> None:
    with pytest.raises(ValueError, match="Fernet key"):
        seal(b"profile-bytes", "not-a-key")


async def test_lease_acquire_release_roundtrip() -> None:
    redis = FakeRedis()
    assert await acquire_lease(redis, "lease", "owner-a") is True
    await release_lease(redis, "lease", "owner-a")
    assert await acquire_lease(redis, "lease", "owner-b") is True


async def test_lease_blocks_second_owner() -> None:
    redis = FakeRedis()
    assert await acquire_lease(redis, "lease", "owner-a") is True
    assert await acquire_lease(redis, "lease", "owner-b") is False


async def test_lease_release_by_stranger_keeps_lease() -> None:
    redis = FakeRedis()
    await acquire_lease(redis, "lease", "owner-a")
    await release_lease(redis, "lease", "owner-b")
    assert await acquire_lease(redis, "lease", "owner-c") is False


async def test_beat_writes_and_clears_key() -> None:
    redis = FakeRedis()
    await beat(redis, "bot_x")
    assert await redis.get(heartbeat_key("bot_x")) == "1"
    await clear_beat(redis, "bot_x")
    assert await redis.get(heartbeat_key("bot_x")) is None
