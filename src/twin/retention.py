import asyncio
from datetime import datetime

import structlog

from twin.core.config import get_settings
from twin.core.time import utcnow
from twin.storage.blob import BlobStore, MinioBlobStore

logger = structlog.get_logger(__name__)

RECORDINGS_PREFIX = "recordings/"


def expired(modified: datetime, now: datetime, retention_days: int) -> bool:
    return (now - modified).days > retention_days


async def sweep(blob: BlobStore, prefix: str, retention_days: int, dry_run: bool) -> dict:
    now = utcnow()
    scanned = deleted = kept = 0
    freed = 0
    for entry in await blob.list(prefix):
        scanned += 1
        if not expired(entry.modified, now, retention_days):
            kept += 1
            continue
        if dry_run:
            deleted += 1
            freed += entry.size
            continue
        try:
            await blob.remove(entry.key)
        except Exception as err:
            logger.warning("retention.delete_failed", key=entry.key, error=str(err))
            continue
        deleted += 1
        freed += entry.size
    return {"scanned": scanned, "deleted": deleted, "kept": kept, "freed_bytes": freed}


async def _run() -> dict:
    settings = get_settings()
    blob = MinioBlobStore(
        settings.blob_endpoint,
        settings.blob_access_key,
        settings.blob_secret_key,
        settings.blob_bucket,
    )
    return await sweep(blob, RECORDINGS_PREFIX, settings.retention_days, settings.retention_dry_run)


def main() -> None:
    summary = asyncio.run(_run())
    logger.info("retention.done", **summary)


if __name__ == "__main__":
    main()
