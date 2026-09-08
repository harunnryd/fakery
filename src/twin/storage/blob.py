import asyncio
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(slots=True)
class BlobObject:
    key: str
    size: int
    modified: datetime


class BlobStore(Protocol):
    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str: ...

    async def get(self, key: str) -> bytes: ...

    async def list(self, prefix: str) -> list[BlobObject]: ...

    async def remove(self, key: str) -> None: ...


class MinioBlobStore:
    def __init__(self, endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> None:
        from minio import Minio

        secure = endpoint_url.startswith("https://")
        endpoint = endpoint_url.split("://", 1)[1]
        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket

    def _ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        await asyncio.to_thread(self._ensure_bucket)
        await asyncio.to_thread(
            self._client.put_object, self._bucket, key, io.BytesIO(data), len(data), content_type
        )
        return f"s3://{self._bucket}/{key}"

    async def get(self, key: str) -> bytes:
        response = await asyncio.to_thread(self._client.get_object, self._bucket, key)
        try:
            return await asyncio.to_thread(response.read)
        finally:
            await asyncio.to_thread(response.close)
            await asyncio.to_thread(response.release_conn)

    async def list(self, prefix: str) -> list[BlobObject]:
        objects = await asyncio.to_thread(
            lambda: list(self._client.list_objects(self._bucket, prefix=prefix, recursive=True))
        )
        return [
            BlobObject(key=obj.object_name or "", size=obj.size or 0, modified=obj.last_modified)
            for obj in objects
            if obj.object_name is not None and obj.last_modified is not None
        ]

    async def remove(self, key: str) -> None:
        await asyncio.to_thread(self._client.remove_object, self._bucket, key)
