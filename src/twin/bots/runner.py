# Lifecycle: JOINING → (open + join) → JOINED → RECORDING → (ends) →
# PROCESSING → COMPLETED; FAILED carries an honest error_code at every exit.
# Recording is best-effort: a capture failure never fails a run.
import hashlib
from pathlib import Path
from typing import Any

import structlog

from twin.bots.state import BotStatus
from twin.core.config import Settings
from twin.meet import join_flow
from twin.meet.launcher import EngineConfig, PatchrightBrowser
from twin.meet.recorder import RECORDER_HOOK, MeetingRecorder
from twin.storage.blob import BlobStore

logger = structlog.get_logger(__name__)


async def execute_run(
    bot_id: str,
    session_factory,
    settings: Settings,
    blob: BlobStore | None = None,
    redis=None,
) -> None:
    from twin.bots.queue import is_cancelled
    from twin.bots.service import BotService, SqlalchemyBotRepository
    from twin.storage.database import session_scope

    async with session_scope(session_factory) as session:
        service = BotService(SqlalchemyBotRepository(session))
        run = await service.get(bot_id)
        display_name = run.display_name or settings.bot_display_name
        meeting_url = run.meeting_url
        if redis is not None and await is_cancelled(redis, bot_id):
            await fail_run(bot_id, session_factory, "cancelled")
            return
        await service.advance(bot_id, BotStatus.JOINING)

    def should_stop() -> Any:
        return is_cancelled(redis, bot_id) if redis is not None else False

    profile_dir = Path(settings.browser_profile_dir).expanduser()
    browser = PatchrightBrowser(
        EngineConfig(
            profile_dir=profile_dir,
            headed=settings.browser_headed,
            locale=settings.bot_locale,
            timezone=settings.bot_timezone,
            guest=not profile_dir.exists(),
            init_scripts=(RECORDER_HOOK,) if settings.record_audio else (),
        )
    )
    recorder: MeetingRecorder | None = None
    recording = b""
    try:
        page = await browser.open(meeting_url)
        await join_flow.join_meeting(page, meeting_url, display_name)
        await _advance(bot_id, session_factory, BotStatus.JOINED)

        outcome = await join_flow.wait_admitted(page)
        logger.info("run.admitted", bot_id=bot_id, outcome=outcome)
        await _advance(bot_id, session_factory, BotStatus.RECORDING)

        if settings.record_audio:
            recorder = MeetingRecorder(page)
            await recorder.start()

        reason = await join_flow.wait_meeting_ended(
            page,
            max_seconds=settings.meeting_max_minutes * 60,
            should_stop=should_stop if redis is not None else None,
        )
        logger.info("run.meeting_ended", bot_id=bot_id, reason=reason)

        if recorder is not None:
            recording = await recorder.stop()
        await join_flow.leave_meeting(page)
        await _complete(bot_id, session_factory, settings, blob, recording)
        logger.info("run.completed", bot_id=bot_id, recording_bytes=len(recording))
    finally:
        await browser.close()


async def _advance(bot_id: str, session_factory, status) -> None:
    from twin.bots.service import BotService, SqlalchemyBotRepository
    from twin.storage.database import session_scope

    async with session_scope(session_factory) as session:
        service = BotService(SqlalchemyBotRepository(session))
        await service.advance(bot_id, status)


async def _complete(
    bot_id: str, session_factory, settings: Settings, blob, recording: bytes
) -> None:
    from twin.bots.service import BotService, SqlalchemyBotRepository
    from twin.storage.database import session_scope

    async with session_scope(session_factory) as session:
        service = BotService(SqlalchemyBotRepository(session))
        run = await service.get(bot_id)
        await service.advance(bot_id, BotStatus.PROCESSING)
        if recording:
            digest = hashlib.sha256(recording).hexdigest()
            run.recording_sha256 = digest
            if blob is not None:
                run.recording_uri = await blob.put(
                    f"recordings/{bot_id}.webm", recording, "audio/webm"
                )
        await service.advance(bot_id, BotStatus.COMPLETED)


async def fail_run(bot_id: str, session_factory, code: str) -> None:
    """Terminal honest failure; no-op if the run already reached an end state."""
    from twin.bots.service import BotService, SqlalchemyBotRepository
    from twin.bots.state import BotStatus
    from twin.storage.database import session_scope

    async with session_scope(session_factory) as session:
        service = BotService(SqlalchemyBotRepository(session))
        run = await service.get(bot_id)
        if BotStatus(run.status) in (BotStatus.COMPLETED, BotStatus.FAILED):
            return
        run.error_code = code
        await service.advance(bot_id, BotStatus.FAILED)
