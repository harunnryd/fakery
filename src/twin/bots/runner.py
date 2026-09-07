import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from twin.bots.models import BotRun
from twin.bots.queue import (
    acquire_lease,
    beat,
    clear_beat,
    heartbeat_key,
    is_cancelled,
    lease_key,
    release_lease,
)
from twin.bots.service import BotService, SqlalchemyBotRepository
from twin.bots.state import LIVE_STATUSES, BotStatus
from twin.core.config import Settings
from twin.core.time import utcnow
from twin.meet import join_flow
from twin.meet.launcher import CloakBrowser, EngineConfig
from twin.meet.profile_prep import init_profile_defaults
from twin.meet.recorder import RECORDER_HOOK, MeetingRecorder, arm_via_cdp_eval
from twin.storage.blob import BlobStore
from twin.storage.database import session_scope
from twin.storage.profiles import pack_profile, profile_key, seal, unpack_profile, unseal

logger = structlog.get_logger(__name__)

PLATFORM_MEET = "meet"
TIER_SIGNED = "signed"
TIER_GUEST = "guest"
HEARTBEAT_INTERVAL_S = 30
PROFILE_CONTENT_TYPE = "application/gzip"


@dataclass(slots=True)
class RunContext:
    session_factory: async_sessionmaker
    settings: Settings
    blob: BlobStore | None
    redis: Redis | None
    owner: str = ""


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
    profile_dir = launch.config.profile_dir
    lease = lease_key(settings.tenant, PLATFORM_MEET, tier)
    if context.redis is not None and not await acquire_lease(context.redis, lease, context.owner):
        await fail_run(bot_id, context, "profile-busy")
        return
    await _restore_profile(context, tier, profile_dir)
    heartbeat: asyncio.Task | None = None
    try:
        async with session_scope(context.session_factory) as session:
            await _service(session).advance(bot_id, BotStatus.JOINING)
        if context.redis is not None:
            heartbeat = asyncio.create_task(_beat_loop(context.redis, bot_id))
        browser = CloakBrowser(launch.config)
        try:
            page = await browser.open(meeting_url)
        except Exception:
            await browser.close()
            raise
        try:
            recording = await _attend_and_record(
                page, bot_id, context, meeting_url, display_name, launch
            )
            await join_flow.leave_meeting(page)
            await _complete(bot_id, context, recording)
            logger.info("run.completed", bot_id=bot_id, recording_bytes=len(recording))
        except join_flow.JoinError:
            await _capture_gate(page, bot_id, context.blob)
            raise
        finally:
            await browser.close()
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        if context.redis is not None:
            try:
                await clear_beat(context.redis, bot_id)
            except Exception as err:
                logger.warning("run.beat_clear_failed", bot_id=bot_id, error=str(err))
        await _persist_profile(context, tier, profile_dir)
        if context.redis is not None:
            try:
                await release_lease(context.redis, lease, context.owner)
            except Exception as err:
                logger.warning("run.lease_release_failed", bot_id=bot_id, error=str(err))


@dataclass(slots=True)
class PreparedLaunch:
    config: EngineConfig
    warmup: bool


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


async def _attend_and_record(
    page: Any,
    bot_id: str,
    context: RunContext,
    meeting_url: str,
    display_name: str,
    launch: PreparedLaunch,
) -> bytes:
    pre_knock = None
    if context.settings.record_audio:

        async def pre_knock(page: Any) -> None:
            await arm_via_cdp_eval(page)

    await join_flow.join_meeting(
        page,
        meeting_url,
        display_name,
        guest=launch.config.guest,
        warmup=launch.warmup,
        pre_knock=pre_knock,
    )
    await _advance(bot_id, context, BotStatus.JOINED)

    outcome = await join_flow.wait_admitted(page)
    logger.info("run.admitted", bot_id=bot_id, outcome=outcome)
    await _advance(bot_id, context, BotStatus.RECORDING)

    recorder: MeetingRecorder | None = None
    if context.settings.record_audio:
        recorder = MeetingRecorder(page)
        await recorder.start()
        asyncio.create_task(_participant_probe(page))

    reason = await join_flow.wait_meeting_ended(
        page,
        max_seconds=context.settings.meeting_max_minutes * 60,
        should_stop=_should_stop(context, bot_id) if context.redis is not None else None,
    )
    logger.info("run.meeting_ended", bot_id=bot_id, reason=reason)

    return await recorder.stop() if recorder is not None else b""


async def _participant_probe(page: Any) -> None:
    await asyncio.sleep(30)
    try:
        await join_flow.log_participant_signals(page)
    except Exception as err:
        logger.warning("probe.participants_failed", error=str(err))


async def _advance(bot_id: str, context: RunContext, status: BotStatus) -> None:
    async with session_scope(context.session_factory) as session:
        await _service(session).advance(bot_id, status)


async def _complete(bot_id: str, context: RunContext, recording: bytes) -> None:
    async with session_scope(context.session_factory) as session:
        service = _service(session)
        run = await service.get(bot_id)
        await service.advance(bot_id, BotStatus.PROCESSING)
        if recording:
            run.recording_sha256 = hashlib.sha256(recording).hexdigest()
            if context.blob is not None:
                run.recording_uri = await context.blob.put(
                    f"recordings/{bot_id}.webm", recording, "audio/webm"
                )
        await service.advance(bot_id, BotStatus.COMPLETED)


async def _capture_gate(page: Any, bot_id: str, blob: BlobStore | None) -> None:
    try:
        shot = await page.screenshot()
    except Exception:
        return
    if blob is None or not shot:
        return
    try:
        uri = await blob.put(f"gates/{bot_id}.png", bytes(shot), "image/png")
    except Exception as err:
        logger.warning("gate.upload_failed", bot_id=bot_id, error=str(err))
        return
    logger.info("gate.captured", bot_id=bot_id, uri=uri)


def _missing_blob_key(err: Exception) -> bool:
    return getattr(err, "code", "") == "NoSuchKey"


async def _restore_profile(context: RunContext, tier: str, profile_dir: Path) -> None:
    if context.blob is None:
        logger.warning("profile.restore_skipped", reason="no-blob")
        return
    key = profile_key(context.settings.tenant, PLATFORM_MEET, tier)
    try:
        data = await context.blob.get(key)
    except Exception as err:
        if _missing_blob_key(err):
            return
        logger.warning("profile.restore_failed", error=str(err))
        return
    unpack_profile(unseal(data, context.settings.profile_encryption_key or None), profile_dir)


async def _persist_profile(context: RunContext, tier: str, profile_dir: Path) -> None:
    if context.blob is None:
        return
    key = profile_key(context.settings.tenant, PLATFORM_MEET, tier)
    try:
        sealed = seal(pack_profile(profile_dir), context.settings.profile_encryption_key or None)
        await context.blob.put(key, sealed, PROFILE_CONTENT_TYPE)
    except Exception as err:
        logger.warning("profile.persist_failed", error=str(err))


async def _beat_loop(redis: Redis, bot_id: str) -> None:
    while True:
        try:
            await beat(redis, bot_id)
        except Exception as err:
            logger.warning("run.heartbeat_failed", bot_id=bot_id, error=str(err))
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


async def fail_run(bot_id: str, context: RunContext, code: str) -> None:
    async with session_scope(context.session_factory) as session:
        service = _service(session)
        run = await service.get(bot_id)
        if BotStatus(run.status) in (BotStatus.COMPLETED, BotStatus.FAILED):
            return
        run.error_code = code
        await service.advance(bot_id, BotStatus.FAILED)


def _should_stop(context: RunContext, bot_id: str):
    async def check() -> bool:
        return await is_cancelled(context.redis, bot_id)

    return check


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
