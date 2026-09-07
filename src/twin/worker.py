import asyncio
import os
import time
from pathlib import Path

import structlog
from redis.asyncio import from_url

from twin.bots.queue import ack_run, claim_run, ensure_group, reclaim_stale
from twin.bots.runner import RunContext, execute_run, fail_run, sweep_orphans
from twin.core.config import get_settings
from twin.storage.blob import MinioBlobStore
from twin.storage.database import create_engine_and_sessionmaker
from twin.storage.profiles import validate_key

CONSUMER = f"worker-{os.getpid()}"
ANTI_CHURN_GUEST_GAP_S = 240
SWEEP_INTERVAL_S = 60
ORPHAN_STALE_S = 300
logger = structlog.get_logger(__name__)


def _error_code(err: Exception) -> str:
    code = getattr(err, "code", None)
    return str(code) if code else "internal"


async def main() -> None:
    settings = get_settings()
    if settings.profile_encryption_key:
        validate_key(settings.profile_encryption_key)
    engine, session_factory = create_engine_and_sessionmaker(settings.database_url)
    redis = from_url(settings.redis_url, decode_responses=True)
    blob = MinioBlobStore(
        settings.blob_endpoint,
        settings.blob_access_key,
        settings.blob_secret_key,
        settings.blob_bucket,
    )
    context = RunContext(
        session_factory=session_factory,
        settings=settings,
        blob=blob,
        redis=redis,
        owner=CONSUMER,
    )
    guest_tier = not Path(settings.browser_profile_dir).expanduser().exists()
    await ensure_group(redis)
    logger.info("worker.started", consumer=CONSUMER, guest_tier=guest_tier)

    last_claim_at = 0.0
    last_sweep_at = 0.0
    try:
        while True:
            if time.monotonic() - last_sweep_at >= SWEEP_INTERVAL_S:
                last_sweep_at = time.monotonic()
                try:
                    await sweep_orphans(session_factory, redis, ORPHAN_STALE_S)
                except Exception as err:
                    logger.warning("worker.sweep_failed", error=str(err))
            claimed = await reclaim_stale(redis, CONSUMER) or await claim_run(redis, CONSUMER)
            if claimed is None:
                continue
            entry_id, bot_id = claimed
            gap = ANTI_CHURN_GUEST_GAP_S if guest_tier else 0
            early = gap - (time.monotonic() - last_claim_at)
            if early > 0:
                logger.info("worker.pacing", wait_seconds=round(early))
                await asyncio.sleep(early)
            last_claim_at = time.monotonic()
            logger.info("run.claimed", bot_id=bot_id)
            try:
                await execute_run(bot_id, context)
            except Exception as err:
                logger.error("run.failed", bot_id=bot_id, error=str(err))
                await fail_run(bot_id, context, _error_code(err))
            finally:
                await ack_run(redis, entry_id)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
