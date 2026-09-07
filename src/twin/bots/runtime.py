import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from twin.bots.queue import is_cancelled
from twin.bots.service import BotService, SqlalchemyBotRepository
from twin.bots.state import BotStatus
from twin.core.config import Settings
from twin.meet import join_flow
from twin.meet.launcher import CloakBrowser, EngineConfig
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
    owner: str = ""


@dataclass(slots=True)
class PreparedLaunch:
    config: EngineConfig
    warmup: bool


class BotRuntime(Protocol):
    async def spawn(
        self,
        bot_id: str,
        meeting_url: str,
        display_name: str,
        context: RunContext,
        launch: PreparedLaunch,
    ) -> None: ...


class ProcessRuntime:
    async def spawn(
        self,
        bot_id: str,
        meeting_url: str,
        display_name: str,
        context: RunContext,
        launch: PreparedLaunch,
    ) -> None:
        browser = CloakBrowser(launch.config)
        try:
            page = await browser.open(meeting_url)
        except Exception:
            await browser.close()
            raise
        try:
            recording, reason = await _attend_and_record(
                page, bot_id, context, meeting_url, display_name, launch
            )
            await join_flow.leave_meeting(page)
            await _complete(bot_id, context, recording, cancelled=reason == join_flow.END_CANCELLED)
            logger.info("run.completed", bot_id=bot_id, recording_bytes=len(recording))
        except join_flow.JoinError:
            await _capture_gate(page, bot_id, context.blob)
            raise
        finally:
            await browser.close()


def launch_runtime(kind: str) -> BotRuntime:
    match kind:
        case "process":
            return ProcessRuntime()
        case _:
            raise ValueError(f"unknown bot runtime: {kind}")


def _service(session) -> BotService:
    return BotService(SqlalchemyBotRepository(session))


async def _attend_and_record(
    page: Any,
    bot_id: str,
    context: RunContext,
    meeting_url: str,
    display_name: str,
    launch: PreparedLaunch,
) -> tuple[bytes, str]:
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

    recording = await recorder.stop() if recorder is not None else b""
    return recording, reason


async def _participant_probe(page: Any) -> None:
    await asyncio.sleep(30)
    try:
        await join_flow.log_participant_signals(page)
    except Exception as err:
        logger.warning("probe.participants_failed", error=str(err))


async def _advance(bot_id: str, context: RunContext, status: BotStatus) -> None:
    async with session_scope(context.session_factory) as session:
        await _service(session).advance(bot_id, status)


async def _complete(bot_id: str, context: RunContext, recording: bytes, cancelled: bool) -> None:
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
        if cancelled:
            run.error_code = "cancelled"
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


def _should_stop(context: RunContext, bot_id: str):
    async def check() -> bool:
        return await is_cancelled(context.redis, bot_id)

    return check
