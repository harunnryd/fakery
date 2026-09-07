from datetime import datetime, timedelta
from pathlib import Path

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from twin.bots.models import BotRun
from twin.bots.queue import acquire_lease, heartbeat_key, is_cancelled, lease_key, release_lease
from twin.bots.runtime import (
    PLATFORM_MEET,
    TIER_GUEST,
    TIER_SIGNED,
    PreparedLaunch,
    RunContext,
    fail_run,
    launch_runtime,
)
from twin.bots.service import BotService, SqlalchemyBotRepository
from twin.bots.state import LIVE_STATUSES, BotStatus
from twin.core.config import Settings
from twin.core.time import utcnow
from twin.meet.launcher import EngineConfig
from twin.meet.profile_prep import init_profile_defaults
from twin.meet.recorder import RECORDER_HOOK
from twin.storage.database import session_scope

logger = structlog.get_logger(__name__)


def _service(session) -> BotService:
    return BotService(SqlalchemyBotRepository(session))


async def execute_run(bot_id: str, context: RunContext) -> None:
    settings = context.settings
    async with session_scope(context.session_factory) as session:
        service = _service(session)
        run = await service.get(bot_id)
        display_name = run.display_name or settings.bot_display_name
        meeting_url = run.meeting_url
        if context.redis is not None and await is_cancelled(context.redis, bot_id):
            await fail_run(bot_id, context, "cancelled")
            return

    launch = _prepare_launch(settings)
    tier = TIER_GUEST if launch.config.guest else TIER_SIGNED
    lease = lease_key(settings.tenant, PLATFORM_MEET, tier)
    if context.redis is not None and not await acquire_lease(context.redis, lease, context.owner):
        await fail_run(bot_id, context, "profile-busy")
        return
    runtime = launch_runtime(settings.bot_runtime, namespace=settings.pod_namespace)
    try:
        await runtime.spawn(bot_id, meeting_url, display_name, context, launch)
    finally:
        if context.redis is not None:
            try:
                await release_lease(context.redis, lease, context.owner)
            except Exception as err:
                logger.warning("run.lease_release_failed", bot_id=bot_id, error=str(err))


def _prepare_launch(settings: Settings) -> PreparedLaunch:
    signed_dir = Path(settings.browser_profile_dir).expanduser()
    guest = not signed_dir.exists()
    profile_dir = signed_dir if not guest else Path(settings.browser_guest_profile_dir).expanduser()
    cookies_db = profile_dir / "Default" / "Network" / "Cookies"
    init_profile_defaults(profile_dir)
    config = EngineConfig(
        profile_dir=profile_dir,
        headed=settings.browser_headed,
        locale=settings.bot_locale,
        timezone=settings.bot_timezone,
        guest=guest,
        init_scripts=(RECORDER_HOOK,),
    )
    return PreparedLaunch(config=config, warmup=not cookies_db.exists())


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
