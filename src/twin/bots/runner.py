import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from twin.bots.queue import is_cancelled
from twin.bots.service import BotService, SqlalchemyBotRepository
from twin.bots.state import BotStatus
from twin.core.config import Settings
from twin.meet import join_flow
from twin.meet.launcher import CloakBrowser, EngineConfig
from twin.meet.profile_prep import init_profile_defaults
from twin.meet.recorder import MeetingRecorder, arm_via_cdp_eval
from twin.storage.blob import BlobStore
from twin.storage.database import session_scope

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class RunContext:
    session_factory: async_sessionmaker
    settings: Settings
    blob: BlobStore | None
    redis: Redis | None


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
        await service.advance(bot_id, BotStatus.JOINING)

    launch = _prepare_launch(settings)
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
        try:
            await page.screenshot(path=f"/tmp/fakery_gate_{bot_id}.png")
        except Exception:
            pass
        raise
    finally:
        await browser.close()


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
