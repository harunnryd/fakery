# Density scaling = more replicas of this worker; one process drives one run
# at a time. Orphaned entries (dead worker) are reclaimed via XAUTOCLAIM.
import asyncio
import os

import structlog
from redis.asyncio import from_url

from twin.bots.queue import ack_run, claim_run, ensure_group, reclaim_stale
from twin.bots.runner import execute_run, fail_run
from twin.core.config import get_settings
from twin.storage.blob import MinioBlobStore
from twin.storage.database import create_engine_and_sessionmaker

CONSUMER = f"worker-{os.getpid()}"
logger = structlog.get_logger(__name__)


def _error_code(err: Exception) -> str:
    code = getattr(err, "code", None)
    return str(code) if code else "internal"


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
    await ensure_group(redis)
    logger.info("worker.started", consumer=CONSUMER)

    try:
        while True:
            claimed = await reclaim_stale(redis, CONSUMER) or await claim_run(redis, CONSUMER)
            if claimed is None:
                continue
            entry_id, bot_id = claimed
            logger.info("run.claimed", bot_id=bot_id)
            try:
                await execute_run(bot_id, session_factory, settings, blob, redis)
            except Exception as err:
                logger.error("run.failed", bot_id=bot_id, error=str(err))
                await fail_run(bot_id, session_factory, _error_code(err))
            finally:
                await ack_run(redis, entry_id)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
