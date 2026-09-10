from datetime import UTC, datetime, timedelta

import pytest

from twin.retention import RECORDINGS_PREFIX, expired, sweep
from twin.storage.blob import BlobObject


def _object(key: str, age_days: int, size: int = 100) -> BlobObject:
    return BlobObject(
        key=key,
        size=size,
        modified=datetime(2026, 9, 7, 12, 0, tzinfo=UTC) - timedelta(days=age_days),
    )


class FakeBlob:
    def __init__(self, objects: list[BlobObject], fail_on: str = "") -> None:
        self._objects = objects
        self._fail_on = fail_on
        self.removed: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        raise AssertionError("no writes expected")

    async def get(self, key: str) -> bytes:
        raise AssertionError("no reads expected")

    async def list(self, prefix: str) -> list[BlobObject]:
        assert prefix == RECORDINGS_PREFIX
        return self._objects

    async def remove(self, key: str) -> None:
        if key == self._fail_on:
            raise RuntimeError("gone")
        self.removed.append(key)


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [(31, True), (30, True), (0, False)],
    ids=["expired", "boundary-expired", "fresh"],
)
def test_expired(age_days: int, expected: bool) -> None:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    modified = now - timedelta(days=age_days)
    assert expired(modified, now, 30) is expected


async def test_sweep_deletes_only_expired() -> None:
    blob = FakeBlob([_object("a", 40, 10), _object("b", 5, 20), _object("c", 90, 30)])
    summary = await sweep(blob, RECORDINGS_PREFIX, 30, dry_run=False)
    assert blob.removed == ["a", "c"]
    assert summary == {
        "scanned": 3,
        "deleted": 2,
        "would_delete": 0,
        "kept": 1,
        "failed": 0,
        "freed_bytes": 40,
    }


async def test_sweep_dry_run_deletes_nothing() -> None:
    blob = FakeBlob([_object("a", 40, 10)])
    summary = await sweep(blob, RECORDINGS_PREFIX, 30, dry_run=True)
    assert blob.removed == []
    assert summary["deleted"] == 0
    assert summary["would_delete"] == 1


async def test_sweep_tolerates_delete_failure() -> None:
    blob = FakeBlob([_object("a", 40, 10)], fail_on="a")
    summary = await sweep(blob, RECORDINGS_PREFIX, 30, dry_run=False)
    assert blob.removed == []
    assert summary == {
        "scanned": 1,
        "deleted": 0,
        "would_delete": 0,
        "kept": 0,
        "failed": 1,
        "freed_bytes": 0,
    }
