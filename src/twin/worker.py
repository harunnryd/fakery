import asyncio
import os
import time
from pathlib import Path

import structlog
from redis.asyncio import from_url

from twin.bots.queue import (
    ack_run,
    claim_run,
    ensure_group,
    is_cancelled,
    reclaim_stale,
    relay_queued,
)
from twin.bots.runner import execute_run, sweep_capacity, sweep_orphans
from twin.bots.runtime import SHUTDOWN_EVENT, build_context, fail_run, handle_sigterm
from twin.core.config import get_settings
from twin.storage.blob import MinioBlobStore
from twin.storage.database import create_engine_and_sessionmaker
from twin.storage.profiles import validate_key

CONSUMER = f"worker-{os.getpid()}"
ANTI_CHURN_GUEST_GAP_S = 240
SWEEP_INTERVAL_S = 60
DISPATCH_RELAY_INTERVAL_S = 2
ORPHAN_STALE_S = 300
WORKER_HEARTBEAT_TTL_S = 60
logger = structlog.get_logger(__name__)


class GuestPacer:
    def __init__(self, gap_s: float) -> None:
        self._gap_s = gap_s
        self._next_at = 0.0

    def reserve(self, now: float) -> tuple[float, float]:
        slot_at = max(now, self._next_at)
        self._next_at = slot_at + self._gap_s
        return slot_at - now, slot_at

    def release(self, slot_at: float) -> None:
        if self._next_at == slot_at + self._gap_s:
            self._next_at = slot_at


def _error_code(err: Exception) -> str:
    code = getattr(err, "code", None)
    return str(code) if code else "internal"


async def main() -> None:
    handle_sigterm()
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
    context = build_context(session_factory, settings, blob, redis, CONSUMER)
    guest_tier = not Path(settings.browser_profile_dir).expanduser().exists()
    await ensure_group(redis)
    logger.info("worker.started", consumer=CONSUMER, guest_tier=guest_tier)

    last_sweep_at = 0.0
    last_relay_at = 0.0
    pacing_lock = asyncio.Lock()
    guest_pacer = GuestPacer(ANTI_CHURN_GUEST_GAP_S if guest_tier else 0)
    tasks: set[asyncio.Task] = set()

    async def run_claim(entry_id: str, bot_id: str) -> None:
        slot_at: float | None = None
        try:
            if SHUTDOWN_EVENT.is_set() or await is_cancelled(redis, bot_id):
                return
            async with pacing_lock:
                wait, slot_at = guest_pacer.reserve(time.monotonic())
            if wait > 0:
                logger.info("worker.pacing", wait_seconds=round(wait))
                deadline = time.monotonic() + wait
                while time.monotonic() < deadline:
                    if SHUTDOWN_EVENT.is_set() or await is_cancelled(redis, bot_id):
                        async with pacing_lock:
                            guest_pacer.release(slot_at)
                        return
                    await asyncio.sleep(min(1.0, deadline - time.monotonic()))
            logger.info("run.claimed", bot_id=bot_id)
            await execute_run(bot_id, context)
        except Exception as err:
            logger.error("run.failed", bot_id=bot_id, error=type(err).__name__)
            await fail_run(bot_id, context, _error_code(err))
        finally:
            await ack_run(redis, entry_id)

    try:
        while not SHUTDOWN_EVENT.is_set():
            await redis.set(f"workers:heartbeat:{CONSUMER}", "1", ex=WORKER_HEARTBEAT_TTL_S)
            finished = {task for task in tasks if task.done()}
            tasks.difference_update(finished)
            if time.monotonic() - last_sweep_at >= SWEEP_INTERVAL_S:
                last_sweep_at = time.monotonic()
                try:
                    await sweep_orphans(session_factory, redis, ORPHAN_STALE_S)
                    await sweep_capacity(context)
                except Exception as err:
                    logger.warning("worker.sweep_failed", error=type(err).__name__)
            if time.monotonic() - last_relay_at >= DISPATCH_RELAY_INTERVAL_S:
                last_relay_at = time.monotonic()
                try:
                    await relay_queued(redis, session_factory)
                except Exception as err:
                    logger.warning("worker.relay_failed", error=type(err).__name__)
            if len(tasks) >= settings.max_concurrent_runs:
                await asyncio.sleep(0.2)
                continue
            claimed = await reclaim_stale(redis, CONSUMER) or await claim_run(redis, CONSUMER)
            if claimed is None:
                continue
            entry_id, bot_id = claimed
            tasks.add(asyncio.create_task(run_claim(entry_id, bot_id)))
    finally:
        if tasks:
            await asyncio.wait(tasks, timeout=120)
            for task in tasks:
                if not task.done():
                    task.cancel()
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
