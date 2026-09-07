from datetime import datetime, timedelta

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from twin.bots.models import BotRun
from twin.bots.queue import acquire_lease, heartbeat_key, is_cancelled, lease_key, release_lease
from twin.bots.runtime import PLATFORM_MEET, RunContext, _tier, fail_run, launch_runtime
from twin.bots.state import LIVE_STATUSES, BotStatus
from twin.core.time import utcnow
from twin.storage.database import session_scope

logger = structlog.get_logger(__name__)


async def execute_run(bot_id: str, context: RunContext) -> None:
    settings = context.settings
    if context.redis is not None and await is_cancelled(context.redis, bot_id):
        await fail_run(bot_id, context, "cancelled")
        return

    lease = lease_key(settings.tenant, PLATFORM_MEET, _tier(settings))
    if context.redis is not None and not await acquire_lease(context.redis, lease, context.owner):
        await fail_run(bot_id, context, "profile-busy")
        return
    runtime = launch_runtime(settings.bot_runtime, namespace=settings.pod_namespace)
    try:
        await runtime.spawn(bot_id, context)
    finally:
        if context.redis is not None:
            try:
                await release_lease(context.redis, lease, context.owner)
            except Exception as err:
                logger.warning("run.lease_release_failed", bot_id=bot_id, error=str(err))


def is_orphan(
    status: BotStatus, updated_at: datetime, heartbeat: bool, now: datetime, stale_after_s: int
) -> bool:
    if status not in LIVE_STATUSES or heartbeat:
        return False
    return (now - updated_at).total_seconds() > stale_after_s


async def sweep_orphans(
    session_factory: async_sessionmaker,
    redis: Redis,
    stale_after_s: int,
    now: datetime | None = None,
) -> int:
    moment = now or utcnow()
    cutoff = moment - timedelta(seconds=stale_after_s)
    live = {status.value for status in LIVE_STATUSES}
    reaped = 0
    async with session_scope(session_factory) as session:
        rows = await session.execute(
            select(BotRun).where(BotRun.status.in_(live), BotRun.updated_at < cutoff)
        )
        for run in list(rows.scalars()):
            if await redis.get(heartbeat_key(run.id)):
                continue
            if not is_orphan(BotStatus(run.status), run.updated_at, False, moment, stale_after_s):
                continue
            run.status = BotStatus.FAILED.value
            run.error_code = "orphaned"
            run.left_at = moment
            reaped += 1
        await session.commit()
    if reaped:
        logger.info("run.sweep_reaped", count=reaped)
    return reaped
