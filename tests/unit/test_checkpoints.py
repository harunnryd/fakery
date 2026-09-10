from datetime import UTC, datetime

from twin.storage.blob import BlobObject
from twin.storage.checkpoints import CheckpointWriter, restore_prefix


class FakeBlob:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str = "") -> str:
        self.data[key] = data
        return f"s3://bucket/{key}"

    async def get(self, key: str) -> bytes:
        return self.data[key]

    async def list(self, prefix: str) -> list[BlobObject]:
        return [BlobObject(key, len(data), datetime.now(UTC)) for key, data in self.data.items()]

    async def remove(self, key: str) -> None:
        self.data.pop(key, None)


async def test_checkpoint_manifest_is_written_after_chunk() -> None:
    blob = FakeBlob()
    writer = CheckpointWriter(blob, "bot_1")
    uri = await writer.append(b"one")
    await writer.append(b"two")
    assert uri.endswith("manifest.json")
    assert "recordings/bot_1/checkpoints/00000000.webm" in blob.data
    restored, partial, checksum = await restore_prefix(blob, uri)
    assert restored == b"onetwo"
    assert partial is False
    assert len(checksum) == 64


async def test_checkpoint_restore_stops_at_corrupt_chunk() -> None:
    blob = FakeBlob()
    writer = CheckpointWriter(blob, "bot_1")
    uri = await writer.append(b"one")
    await writer.append(b"two")
    blob.data["recordings/bot_1/checkpoints/00000001.webm"] = b"bad"
    restored, partial, _ = await restore_prefix(blob, uri)
    assert restored == b"one"
    assert partial is True
