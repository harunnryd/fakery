import asyncio
import uuid
from datetime import datetime, timedelta

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from twin.bots.models import BotRun, CapacityCounter, CapacityReservation
from twin.bots.queue import acquire_lease, heartbeat_key, is_cancelled, lease_key, release_lease
from twin.bots.runtime import (
    PLATFORM_MEET,
    TIER_SIGNED,
    RunContext,
    _service,
    _tier,
    fail_run,
    launch_runtime,
)
from twin.bots.state import LIVE_STATUSES, BotStatus
from twin.core.time import utcnow
from twin.storage.database import session_scope
from twin.webhooks.dispatch import DbOutbox

logger = structlog.get_logger(__name__)

ADMIT_POLL_S = 30
ADMIT_WAIT_S = 600
RESERVATION_STALE_S = 300


async def execute_run(bot_id: str, context: RunContext) -> None:
    settings = context.settings
    if context.redis is not None and await is_cancelled(context.redis, bot_id):
        await fail_run(bot_id, context, "cancelled")
        return
    async with session_scope(context.session_factory) as session:
        run = await _service(session, context).get(bot_id)
        if run.status != BotStatus.QUEUED.value:
            return

    run_owner = f"{context.owner}:{uuid.uuid4().hex}"
    if not await reserve_capacity(context, bot_id, run_owner):
        if not await _has_other_reservation(context, bot_id, run_owner):
            await fail_run(bot_id, context, "at-capacity")
        return
    try:
        lease = lease_key(settings.tenant, PLATFORM_MEET, _tier(settings))
        shared_profile = context.redis is not None and _tier(settings) == TIER_SIGNED
        if shared_profile and not await acquire_lease(context.redis, lease, run_owner):
            await fail_run(bot_id, context, "profile-busy")
            return
        runtime = launch_runtime(settings.bot_runtime, namespace=settings.pod_namespace)
        try:
            await runtime.spawn(bot_id, context)
        finally:
            if shared_profile:
                try:
                    await release_lease(context.redis, lease, run_owner)
                except Exception as err:
                    logger.warning(
                        "run.lease_release_failed", bot_id=bot_id, error=type(err).__name__
                    )
    finally:
        await release_capacity(context, bot_id)


def is_orphan(
    status: BotStatus, updated_at: datetime, heartbeat: bool, now: datetime, stale_after_s: int
) -> bool:
    if status not in LIVE_STATUSES or heartbeat:
        return False
    return (now - updated_at).total_seconds() > stale_after_s


async def count_live_runs(context: RunContext) -> int:
    live = {status.value for status in LIVE_STATUSES}
    async with session_scope(context.session_factory) as session:
        rows = await session.execute(select(BotRun).where(BotRun.status.in_(live)))
        return len(list(rows.scalars()))


async def _wait_for_capacity(context: RunContext, bot_id: str) -> bool:
    waited = 0
    while await count_live_runs(context) >= context.settings.max_concurrent_runs:
        if waited >= ADMIT_WAIT_S:
            return False
        logger.info("run.deferred", bot_id=bot_id, waited_s=waited)
        await asyncio.sleep(ADMIT_POLL_S)
        waited += ADMIT_POLL_S
    return True


async def reserve_capacity(context: RunContext, bot_id: str, owner: str | None = None) -> bool:
    deadline = asyncio.get_running_loop().time() + ADMIT_WAIT_S
    while True:
        if await _try_reserve_capacity(context, bot_id, owner or context.owner):
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        logger.info("run.deferred", bot_id=bot_id)
        await asyncio.sleep(ADMIT_POLL_S)


async def _try_reserve_capacity(context: RunContext, bot_id: str, owner: str) -> bool:
    settings = context.settings
    async with session_scope(context.session_factory) as session:
        counter = await session.get(CapacityCounter, settings.tenant)
        if counter is None:
            counter = CapacityCounter(
                tenant=settings.tenant, active_count=0, limit=settings.max_concurrent_runs
            )
            session.add(counter)
            flush = getattr(session, "flush", None)
            if flush is not None:
                await flush()
        else:
            counter = await session.get(CapacityCounter, settings.tenant, with_for_update=True)
            counter.limit = settings.max_concurrent_runs
        existing = await session.execute(
            select(CapacityReservation).where(
                CapacityReservation.bot_id == bot_id,
                CapacityReservation.released_at.is_(None),
            )
        )
        current = next(iter(existing.scalars()), None)
        if current is not None:
            return current.owner == owner
        if counter.active_count >= counter.limit:
            return False
        session.add(
            CapacityReservation(
                id=f"res_{uuid.uuid4().hex}",
                tenant=settings.tenant,
                bot_id=bot_id,
                owner=owner,
            )
        )
        counter.active_count += 1
        return True


async def _has_other_reservation(context: RunContext, bot_id: str, owner: str) -> bool:
    async with session_scope(context.session_factory) as session:
        rows = await session.execute(
            select(CapacityReservation).where(
                CapacityReservation.bot_id == bot_id,
                CapacityReservation.released_at.is_(None),
            )
        )
        current = next(iter(rows.scalars()), None)
        return current is not None and current.owner != owner


async def release_capacity(context: RunContext, bot_id: str) -> None:
    async with session_scope(context.session_factory) as session:
        rows = await session.execute(
            select(CapacityReservation).where(
                CapacityReservation.bot_id == bot_id,
                CapacityReservation.released_at.is_(None),
            )
        )
        reservation = next(iter(rows.scalars()), None)
        if reservation is None:
            return
        reservation.released_at = utcnow()
        counter = await session.get(CapacityCounter, reservation.tenant, with_for_update=True)
        if counter is not None:
            counter.active_count = max(0, counter.active_count - 1)


async def sweep_capacity(context: RunContext, now: datetime | None = None) -> int:
    moment = now or utcnow()
    released = 0
    async with session_scope(context.session_factory) as session:
        rows = await session.execute(
            select(CapacityReservation).where(CapacityReservation.released_at.is_(None))
        )
        reservations = list(rows.scalars())
        for reservation in reservations:
            run = await session.get(BotRun, reservation.bot_id)
            stale = (
                reservation.acquired_at is not None
                and (moment - reservation.acquired_at).total_seconds() > RESERVATION_STALE_S
            )
            if (
                run is None
                or BotStatus(run.status) in (BotStatus.COMPLETED, BotStatus.FAILED)
                or (run.status == BotStatus.QUEUED.value and stale)
            ):
                reservation.released_at = moment
                counter = await session.get(
                    CapacityCounter, reservation.tenant, with_for_update=True
                )
                if counter is not None:
                    counter.active_count = max(0, counter.active_count - 1)
                released += 1
    return released


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
            run.error_code = "orphaned"
            run.stop_reason = "failure"
            if hasattr(session, "bind"):
                await _service_for_session(session).advance(run.id, BotStatus.FAILED)
            else:
                run.status = BotStatus.FAILED.value
                run.left_at = moment
            reaped += 1
    if reaped:
        logger.info("run.sweep_reaped", count=reaped)
    return reaped


def _service_for_session(session):
    from twin.bots.service import BotService, SqlalchemyBotRepository

    return BotService(SqlalchemyBotRepository(session), events=DbOutbox(session))
