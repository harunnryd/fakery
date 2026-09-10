import asyncio
import signal
from datetime import timedelta

import structlog
from redis.asyncio import from_url
from sqlalchemy import select

from twin.bots.models import BotRun
from twin.core.config import get_settings
from twin.core.time import utcnow
from twin.storage.blob import MinioBlobStore
from twin.storage.checkpoints import restore_prefix
from twin.storage.database import create_engine_and_sessionmaker, session_scope

logger = structlog.get_logger(__name__)

POLL_S = 10
BATCH_SIZE = 20
HEARTBEAT_KEY = "workers:heartbeat:finalizer"
HEARTBEAT_TTL_S = 60


async def recover_once(session_factory, blob, retention_days: int) -> int:
    async with session_scope(session_factory) as session:
        rows = await session.execute(
            select(BotRun)
            .where(
                BotRun.status == "failed",
                BotRun.checkpoint_manifest_uri.is_not(None),
                BotRun.recording_uri.is_(None),
            )
            .order_by(BotRun.updated_at, BotRun.id)
            .limit(BATCH_SIZE)
        )
        candidates = [
            (run.id, run.checkpoint_manifest_uri)
            for run in rows.scalars()
            if run.checkpoint_manifest_uri
        ]
    recovered = 0
    for bot_id, manifest_uri in candidates:
        try:
            recording, _, checksum = await restore_prefix(blob, manifest_uri)
            if not recording:
                continue
            uri = await blob.put(f"recordings/{bot_id}.webm", recording, "audio/webm")
        except Exception as err:
            logger.warning("finalizer.recovery_failed", bot_id=bot_id, error=type(err).__name__)
            continue
        async with session_scope(session_factory) as session:
            run = await session.get(BotRun, bot_id, with_for_update=True)
            if run is None or run.recording_uri is not None or run.status != "failed":
                continue
            run.recording_uri = uri
            run.recording_sha256 = checksum
            run.recording_bytes = len(recording)
            run.recording_expires_at = utcnow() + timedelta(days=retention_days)
            run.recording_status = "partial"
            run.recording_partial = True
            run.finalization_status = "recovered"
            recovered += 1
    return recovered


async def main() -> None:
    settings = get_settings()
    engine, session_factory = create_engine_and_sessionmaker(settings.database_url)
    redis = from_url(settings.redis_url, decode_responses=True)
    blob = MinioBlobStore(
        settings.blob_endpoint,
        settings.blob_access_key,
        settings.blob_secret_key,
        settings.blob_bucket,
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopping.set)
    loop.add_signal_handler(signal.SIGINT, stopping.set)
    logger.info("finalizer.started")
    try:
        while not stopping.is_set():
            await redis.set(HEARTBEAT_KEY, "1", ex=HEARTBEAT_TTL_S)
            try:
                recovered = await recover_once(session_factory, blob, settings.retention_days)
                logger.info("finalizer.sweep", recovered=recovered)
            except Exception as err:
                logger.warning("finalizer.sweep_failed", error=type(err).__name__)
            try:
                await asyncio.wait_for(stopping.wait(), timeout=POLL_S)
            except TimeoutError:
                continue
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
