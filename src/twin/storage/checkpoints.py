import hashlib
import json
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from twin.storage.blob import BlobStore

CHECKPOINT_CONTENT_TYPE = "audio/webm"
MANIFEST_CONTENT_TYPE = "application/json"


@dataclass(slots=True)
class CheckpointChunk:
    sequence: int
    uri: str
    key: str
    size: int
    sha256: str


class CheckpointWriter:
    def __init__(self, blob: BlobStore, bot_id: str) -> None:
        self._blob = blob
        self._bot_id = bot_id
        self._chunks: list[CheckpointChunk] = []
        self._manifest_uri: str | None = None

    @property
    def manifest_uri(self) -> str | None:
        return self._manifest_uri

    async def append(self, data: bytes) -> str:
        if not data:
            return self._manifest_uri or ""
        sequence = len(self._chunks)
        key = f"recordings/{self._bot_id}/checkpoints/{sequence:08d}.webm"
        checksum = hashlib.sha256(data).hexdigest()
        uri = await self._blob.put(key, data, CHECKPOINT_CONTENT_TYPE)
        self._chunks.append(CheckpointChunk(sequence, uri, key, len(data), checksum))
        manifest_key = f"recordings/{self._bot_id}/checkpoints/manifest.json"
        manifest = {
            "version": 1,
            "bot_id": self._bot_id,
            "format": CHECKPOINT_CONTENT_TYPE,
            "chunks": [asdict(chunk) for chunk in self._chunks],
        }
        self._manifest_uri = await self._blob.put(
            manifest_key,
            json.dumps(manifest, sort_keys=True).encode(),
            MANIFEST_CONTENT_TYPE,
        )
        return self._manifest_uri


async def restore_prefix(blob: BlobStore, manifest_uri: str) -> tuple[bytes, bool, str]:
    manifest = json.loads((await blob.get(_key_from_uri(manifest_uri))).decode())
    prefix = bytearray()
    complete = True
    for chunk in manifest.get("chunks", []):
        data = await blob.get(str(chunk["key"]))
        if hashlib.sha256(data).hexdigest() != chunk["sha256"]:
            complete = False
            break
        prefix.extend(data)
    return bytes(prefix), not complete, hashlib.sha256(prefix).hexdigest()


def _key_from_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    return parsed.path.lstrip("/") if parsed.scheme else uri.lstrip("/")
