import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select

from twin.bots.models import BotRun
from twin.core.config import get_settings
from twin.core.time import utcnow
from twin.storage.blob import BlobStore, MinioBlobStore
from twin.storage.database import create_engine_and_sessionmaker, session_scope

logger = structlog.get_logger(__name__)

RECORDINGS_PREFIX = "recordings/"


def expired(modified: datetime, now: datetime, retention_days: int) -> bool:
    return now - modified >= timedelta(days=retention_days)


async def sweep(
    blob: BlobStore,
    prefix: str,
    retention_days: int,
    dry_run: bool,
    on_deleted: Callable[[str], Awaitable[None]] | None = None,
    expires_at_for_key: Callable[[str], datetime | None] | None = None,
) -> dict:
    now = utcnow()
    scanned = deleted = would_delete = kept = failed = 0
    freed = 0
    for entry in await blob.list(prefix):
        scanned += 1
        expiry = expires_at_for_key(entry.key) if expires_at_for_key is not None else None
        is_expired = (
            now >= expiry if expiry is not None else expired(entry.modified, now, retention_days)
        )
        if not is_expired:
            kept += 1
            continue
        if dry_run:
            would_delete += 1
            freed += entry.size
            continue
        try:
            await blob.remove(entry.key)
        except Exception as err:
            logger.warning("retention.delete_failed", key=entry.key, error=type(err).__name__)
            failed += 1
            continue
        deleted += 1
        freed += entry.size
        if on_deleted is not None:
            await on_deleted(entry.key)
    return {
        "scanned": scanned,
        "deleted": deleted,
        "would_delete": would_delete,
        "kept": kept,
        "failed": failed,
        "freed_bytes": freed,
    }


async def _run() -> dict:
    settings = get_settings()
    blob = MinioBlobStore(
        settings.blob_endpoint,
        settings.blob_access_key,
        settings.blob_secret_key,
        settings.blob_bucket,
    )
    engine, session_factory = create_engine_and_sessionmaker(settings.database_url)

    async with session_scope(session_factory) as session:
        rows = await session.execute(
            select(BotRun.id, BotRun.recording_expires_at).where(
                BotRun.recording_expires_at.is_not(None)
            )
        )
        expires_at = dict(rows.all())

    def expiry_for_key(key: str) -> datetime | None:
        bot_id = _bot_id_from_key(key)
        return expires_at.get(bot_id) if bot_id is not None else None

    async def mark_expired(key: str) -> None:
        bot_id = _bot_id_from_key(key)
        if bot_id is None:
            return
        async with session_scope(session_factory) as session:
            run = await session.get(BotRun, bot_id)
            if run is None:
                return
            if "/checkpoints/" not in key:
                run.recording_status = "expired"
                run.finalization_status = "expired"
                run.recording_uri = None
            if key.endswith("manifest.json"):
                run.checkpoint_manifest_uri = None

    try:
        return await sweep(
            blob,
            RECORDINGS_PREFIX,
            settings.retention_days,
            settings.retention_dry_run,
            mark_expired,
            expiry_for_key,
        )
    finally:
        await engine.dispose()


def _bot_id_from_key(key: str) -> str | None:
    relative = key.removeprefix(RECORDINGS_PREFIX)
    if not relative:
        return None
    return relative.split("/", 1)[0].removesuffix(".webm") or None


def main() -> None:
    summary = asyncio.run(_run())
    logger.info("retention.done", **summary)


if __name__ == "__main__":
    main()
