import asyncio
import hashlib
import shutil
import signal
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from twin.bots.jobs import (
    JOB_STARTUP_GRACE_S,
    JobClient,
    K8sJobClient,
    bot_job_name,
    build_bot_job,
    resolve_job_outcome,
)
from twin.bots.queue import beat, clear_beat, is_cancelled
from twin.bots.service import BotService, RecordingArtifact, SqlalchemyBotRepository
from twin.bots.state import BotStatus
from twin.core.config import Settings
from twin.core.time import SECONDS_PER_MINUTE, utcnow
from twin.meet import join_flow
from twin.meet.launcher import CloakBrowser, EngineConfig
from twin.meet.profile_prep import init_profile_defaults
from twin.meet.recorder import RECORDER_HOOK, MeetingRecorder, arm_via_cdp_eval
from twin.notes.summarizer import Summarizer, launch_summarizer, summarize_segments
from twin.storage.blob import BlobStore
from twin.storage.checkpoints import CheckpointWriter, restore_prefix
from twin.storage.database import session_scope
from twin.storage.profiles import pack_profile, profile_key, seal, unpack_profile, unseal
from twin.transcription.decode import decode_webm
from twin.transcription.transcriber import Segment, Transcriber, launch_transcriber
from twin.webhooks.dispatch import DbOutbox, EventSink

logger = structlog.get_logger(__name__)

PLATFORM_MEET = "meet"
TIER_SIGNED = "signed"
TIER_GUEST = "guest"
HEARTBEAT_INTERVAL_S = 30
PROFILE_CONTENT_TYPE = "application/gzip"
TRANSCRIBE_DRAIN_S = 120
BATCH_TRANSCRIBE_TIMEOUT_S = 90
SUMMARY_TIMEOUT_S = 60
JOB_MISSING_GRACE_S = 120
SPEAKER_MIN_SHARE = 0.15
BATCH_COVERAGE_THRESHOLD = 0.25
SUMMARY_ATTEMPTS = 3
LIVE_STT_QUEUE_LIMIT = 30


@dataclass(slots=True)
class RunContext:
    session_factory: async_sessionmaker
    settings: Settings
    blob: BlobStore | None
    redis: Redis | None
    owner: str = ""
    transcriber: Transcriber | None = None
    events: EventSink | None = None
    summarizer: Summarizer | None = None


@dataclass(slots=True)
class PreparedLaunch:
    config: EngineConfig
    warmup: bool


@dataclass(frozen=True, slots=True)
class LiveTranscriptionResult:
    finals: int = 0
    error: str | None = None


class BotRuntime(Protocol):
    async def spawn(self, bot_id: str, context: RunContext) -> None: ...


class ProcessRuntime:
    async def spawn(self, bot_id: str, context: RunContext) -> None:
        async with session_scope(context.session_factory) as session:
            run = await _service(session, context).get(bot_id)
        launch = _prepare_launch(context.settings, bot_id)
        await attend(
            bot_id,
            run.meeting_url,
            run.display_name or context.settings.bot_display_name,
            context,
            launch,
        )


class JobRuntime:
    def __init__(self, jobs: JobClient) -> None:
        self._jobs = jobs

    async def spawn(self, bot_id: str, context: RunContext) -> None:
        settings = context.settings
        namespace = settings.pod_namespace
        name = bot_job_name(bot_id)
        try:
            get_job = getattr(self._jobs, "get_job", None)
            existing = await get_job(name) if get_job is not None else None
            if existing is None:
                await self._jobs.create_job(
                    build_bot_job(
                        bot_id,
                        image=settings.bot_image,
                        namespace=namespace,
                        meeting_max_minutes=settings.meeting_max_minutes,
                    )
                )
                logger.info("job.created", bot_id=bot_id, job=name)
            else:
                logger.info("job.adopted", bot_id=bot_id, job=name)
            timeout_s = settings.meeting_max_minutes * SECONDS_PER_MINUTE + JOB_STARTUP_GRACE_S
            result = await self._jobs.wait_terminal(name, timeout_s)
        finally:
            try:
                await self._jobs.aclose()
            except Exception as err:
                logger.warning("job.close_failed", bot_id=bot_id, error=type(err).__name__)
        if result == "timeout":
            try:
                await self._jobs.delete_job(name)
            except Exception as err:
                logger.warning("job.delete_failed", bot_id=bot_id, error=type(err).__name__)
            if not await _wait_for_db_terminal(context, bot_id):
                await fail_run(bot_id, context, "job-timeout")
            return
        terminal = (
            await _wait_for_db_terminal(context, bot_id)
            if result == "missing"
            else await _db_terminal(context, bot_id)
        )
        code = resolve_job_outcome(result, terminal)
        if code is None:
            return
        logger.warning("job.failed", bot_id=bot_id, result=result)
        await fail_run(bot_id, context, code)


def launch_runtime(
    kind: str, jobs: JobClient | None = None, namespace: str = "fakery"
) -> BotRuntime:
    match kind:
        case "process":
            return ProcessRuntime()
        case "job":
            return JobRuntime(jobs or K8sJobClient(namespace))
        case _:
            raise ValueError(f"unknown bot runtime: {kind}")


def _service(session, context: RunContext | None = None) -> BotService:
    events = context.events if context is not None else None
    if isinstance(events, DbOutbox):
        events = DbOutbox(session)
    return BotService(SqlalchemyBotRepository(session), events=events)


def build_context(
    session_factory: async_sessionmaker,
    settings: Settings,
    blob: BlobStore | None,
    redis: Redis | None,
    owner: str,
) -> RunContext:
    return RunContext(
        session_factory=session_factory,
        settings=settings,
        blob=blob,
        redis=redis,
        owner=owner,
        transcriber=_transcriber(settings),
        events=DbOutbox(session_factory),
        summarizer=_summarizer(settings),
    )


def _summarizer(settings: Settings) -> Summarizer | None:
    if not settings.llm_api_key:
        logger.warning("run.notes_disabled", reason="no-key")
        return None
    return launch_summarizer(settings.llm_provider, settings.llm_api_key, settings.llm_model)


def _transcriber(settings: Settings) -> Transcriber | None:
    if not settings.stt_api_key:
        logger.warning("run.stt_disabled", reason="no-key")
        return None
    return launch_transcriber(
        settings.stt_provider, settings.stt_api_key, settings.stt_diarize, settings.stt_language
    )


def _tier(settings: Settings) -> str:
    signed_dir = Path(settings.browser_profile_dir).expanduser()
    return TIER_SIGNED if signed_dir.exists() else TIER_GUEST


def _prepare_launch(settings: Settings, bot_id: str | None = None) -> PreparedLaunch:
    signed_dir = Path(settings.browser_profile_dir).expanduser()
    guest = not signed_dir.exists()
    guest_root = Path(settings.browser_guest_profile_dir).expanduser()
    profile_dir = signed_dir if not guest else guest_root / (bot_id or "default")
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


async def attend(
    bot_id: str,
    meeting_url: str,
    display_name: str,
    context: RunContext,
    launch: PreparedLaunch,
) -> None:
    tier = TIER_GUEST if launch.config.guest else TIER_SIGNED
    await _restore_profile(context, tier, launch.config.profile_dir)
    heartbeat: asyncio.Task | None = None
    try:
        async with session_scope(context.session_factory) as session:
            await _service(session, context).advance(bot_id, BotStatus.JOINING)
        if context.redis is not None:
            heartbeat = asyncio.create_task(_beat_loop(context.redis, bot_id))
        browser = CloakBrowser(launch.config)
        try:
            page = await browser.open(meeting_url)
        except Exception as err:
            logger.error("run.browser_open_failed", bot_id=bot_id, error=type(err).__name__)
            await browser.close()
            raise
        try:
            try:
                recording, reason, checkpoint_uri, checkpoint_failed = await _attend_and_record(
                    page, bot_id, context, meeting_url, display_name, launch
                )
            except Exception as err:
                logger.error("run.attend_failed", bot_id=bot_id, error=type(err).__name__)
                raise
            if reason == join_flow.END_CANCELLED:
                async with session_scope(context.session_factory) as session:
                    run = await _service(session, context).get(bot_id)
                if BotStatus(run.status) not in {BotStatus.JOINED, BotStatus.RECORDING}:
                    await fail_run(
                        bot_id,
                        context,
                        await _requested_stop_reason(context, bot_id) or "cancelled",
                    )
                    return
            await _persist_recording(
                bot_id, context, recording, checkpoint_uri, checkpoint_failed, reason
            )
            await join_flow.leave_meeting(page)
            await _summarize(bot_id, context)
            stop_reason = await _requested_stop_reason(context, bot_id)
            await _complete(
                bot_id,
                context,
                b"",
                cancelled=reason == join_flow.END_CANCELLED or stop_reason is not None,
                stop_reason=stop_reason,
            )
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
                logger.warning("run.beat_clear_failed", bot_id=bot_id, error=type(err).__name__)
        await _persist_profile(context, tier, launch.config.profile_dir)


async def _db_terminal(context: RunContext, bot_id: str) -> bool:
    async with session_scope(context.session_factory) as session:
        run = await _service(session, context).get(bot_id)
        return BotStatus(run.status) in (BotStatus.COMPLETED, BotStatus.FAILED)


async def _wait_for_db_terminal(context: RunContext, bot_id: str) -> bool:
    async with session_scope(context.session_factory) as session:
        if not hasattr(session, "bind"):
            return BotStatus((await _service(session, context).get(bot_id)).status) in (
                BotStatus.COMPLETED,
                BotStatus.FAILED,
            )
    deadline = asyncio.get_running_loop().time() + JOB_MISSING_GRACE_S
    while True:
        if await _db_terminal(context, bot_id):
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(5)


async def _attend_and_record(
    page: Any,
    bot_id: str,
    context: RunContext,
    meeting_url: str,
    display_name: str,
    launch: PreparedLaunch,
) -> tuple[bytes, str, str | None, bool]:
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
        should_stop=_should_stop(context, bot_id),
    )
    outcome = await join_flow.wait_admitted(page, should_stop=_should_stop(context, bot_id))
    logger.info("run.admitted", bot_id=bot_id, outcome=outcome)
    if outcome == join_flow.END_CANCELLED:
        return b"", outcome, None, False
    await _advance(bot_id, context, BotStatus.JOINED)
    await _advance(bot_id, context, BotStatus.RECORDING)

    recorder: MeetingRecorder | None = None
    checkpoint_writer = CheckpointWriter(context.blob, bot_id) if context.blob is not None else None

    async def checkpoint_sink(sequence: int, data: bytes) -> None:
        if checkpoint_writer is None:
            return
        manifest_uri = await checkpoint_writer.append(data)
        if manifest_uri:
            async with session_scope(context.session_factory) as session:
                await _service(session, context).set_checkpoint_manifest(bot_id, manifest_uri)

    transcriber = context.transcriber if context.settings.record_audio else None
    if transcriber is None:
        await _set_artifact_status(context, bot_id, "transcription_status", "disabled")
    chunk_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=LIVE_STT_QUEUE_LIMIT)
    transcribe_task: asyncio.Task | None = None
    probe_task: asyncio.Task | None = None
    if context.settings.record_audio:

        def push_chunk(data: bytes) -> None:
            if transcriber is None or (transcribe_task is not None and transcribe_task.done()):
                return
            if chunk_queue.full():
                logger.warning("transcribe.queue_full", bot_id=bot_id)
                return
            chunk_queue.put_nowait(data)

        recorder = MeetingRecorder(
            page,
            sink=push_chunk,
            checkpoint_sink=checkpoint_sink if checkpoint_writer is not None else None,
        )
        await recorder.start()
        probe_task = asyncio.create_task(_participant_probe(page))
        if transcriber is not None:
            transcribe_task = asyncio.create_task(
                _transcribe_live(bot_id, context, transcriber, _queue_chunks(chunk_queue))
            )

    reason = await join_flow.wait_meeting_ended(
        page,
        max_seconds=context.settings.meeting_max_minutes * SECONDS_PER_MINUTE,
        should_stop=_should_stop(context, bot_id) if context.redis is not None else None,
    )
    logger.info("run.meeting_ended", bot_id=bot_id, reason=reason)

    recording = await recorder.stop() if recorder is not None else b""
    if probe_task is not None:
        probe_task.cancel()
        try:
            await probe_task
        except asyncio.CancelledError:
            pass
    while chunk_queue.full():
        chunk_queue.get_nowait()
    chunk_queue.put_nowait(None)
    live = await _await_transcript(transcribe_task, bot_id)
    live_count = live.finals
    if recording and transcriber is not None:
        batch = await _batch_segments(bot_id, context, transcriber, recording)
        batch_for_annotation: list[Segment] | None = None
        if batch is None:
            await _set_artifact_status(
                context, bot_id, "transcription_status", "provider-failed", "batch-failed"
            )
        elif not live_count and not batch:
            await _set_artifact_status(
                context, bot_id, "transcription_status", "silence", live.error
            )
        else:
            batch_for_annotation = batch
            if live_count and batch:
                async with session_scope(context.session_factory) as session:
                    current = await _service(session, context).transcript(bot_id)
                live_segments = [
                    Segment(
                        text=row.text,
                        start_ms=row.start_ms,
                        end_ms=row.end_ms,
                        speaker=row.speaker,
                    )
                    for row in current
                ]
                batch = _uncovered_batch_segments(live_segments, batch)
            for segment in batch:
                await _ingest_segment(bot_id, context, segment)
            await _set_artifact_status(context, bot_id, "transcription_status", "ready", live.error)
        if live_count and context.settings.stt_diarize:
            if batch_for_annotation is not None:
                await _annotate_segments(bot_id, context, batch_for_annotation)
    elif recording and context.transcriber is None:
        await _set_artifact_status(context, bot_id, "transcription_status", "disabled")
    return (
        recording,
        reason,
        checkpoint_writer.manifest_uri if checkpoint_writer is not None else None,
        recorder.checkpoint_failed if recorder is not None else False,
    )


async def _queue_chunks(queue: asyncio.Queue[bytes | None]) -> AsyncIterator[bytes]:
    while True:
        chunk = await queue.get()
        if chunk is None:
            return
        yield chunk


async def _ingest_segment(bot_id: str, context: RunContext, segment: Segment) -> None:
    async with session_scope(context.session_factory) as session:
        await _service(session, context).ingest_segment(bot_id, segment)


async def _transcribe_live(
    bot_id: str,
    context: RunContext,
    transcriber: Transcriber,
    chunks: AsyncIterator[bytes],
) -> LiveTranscriptionResult:
    audio = decode_webm(chunks)
    try:
        finals = await transcriber.transcribe_stream(
            audio, partial(_ingest_segment, bot_id, context)
        )
        return LiveTranscriptionResult(finals=finals)
    except Exception as err:
        logger.warning("transcribe.live_failed", bot_id=bot_id, error=type(err).__name__)
        await _set_artifact_status(
            context, bot_id, "transcription_status", "provider-failed", "live-failed"
        )
        return LiveTranscriptionResult(error="live-failed")
    finally:
        await audio.aclose()


async def _await_transcript(task: asyncio.Task | None, bot_id: str) -> LiveTranscriptionResult:
    if task is None:
        return LiveTranscriptionResult()
    try:
        return await asyncio.wait_for(task, TRANSCRIBE_DRAIN_S)
    except TimeoutError as err:
        logger.warning("transcribe.drain_failed", bot_id=bot_id, error=type(err).__name__)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return LiveTranscriptionResult(error="live-drain-timeout")


async def _annotate_segments(bot_id: str, context: RunContext, segments: list[Segment]) -> None:
    if not _confident_split(segments):
        logger.info("transcribe.split_uncertain", bot_id=bot_id)
        return
    async with session_scope(context.session_factory) as session:
        annotated = await _service(session, context).annotate_speakers(bot_id, segments)
    logger.info("transcribe.annotated", bot_id=bot_id, segments=annotated)


async def _batch_segments(
    bot_id: str, context: RunContext, transcriber: Transcriber, recording: bytes
) -> list[Segment] | None:
    try:
        return await asyncio.wait_for(
            transcriber.transcribe_recording(recording), BATCH_TRANSCRIBE_TIMEOUT_S
        )
    except Exception as err:
        logger.warning("transcribe.batch_failed", bot_id=bot_id, error=type(err).__name__)
        return None


async def _set_artifact_status(
    context: RunContext,
    bot_id: str,
    field: str,
    status: str,
    error: str | None = None,
) -> None:
    async with session_scope(context.session_factory) as session:
        artifact = field.removesuffix("_status")
        await _service(session, context).set_artifact_status(bot_id, artifact, status, error)


def _confident_split(segments: list[Segment]) -> bool:
    durations: dict[str, int] = {}
    for segment in segments:
        if segment.speaker is None:
            continue
        durations[segment.speaker] = (
            durations.get(segment.speaker, 0) + segment.end_ms - segment.start_ms
        )
    if len(durations) < 2:
        return False
    total = sum(durations.values())
    return min(durations.values()) / total >= SPEAKER_MIN_SHARE


def _uncovered_batch_segments(live: list[Segment], batch: list[Segment]) -> list[Segment]:
    if not live:
        return batch
    return [segment for segment in batch if not _covered_by_live(segment, live)]


def _covered_by_live(candidate: Segment, live: list[Segment]) -> bool:
    candidate_duration = max(1, candidate.end_ms - candidate.start_ms)
    covered_ranges: list[tuple[int, int]] = []
    for segment in sorted(live, key=lambda item: item.start_ms):
        start = max(candidate.start_ms, segment.start_ms)
        end = min(candidate.end_ms, segment.end_ms)
        if start < end:
            covered_ranges.append((start, end))
    covered = 0
    current_start = current_end = None
    for start, end in covered_ranges:
        if current_start is None:
            current_start, current_end = start, end
        elif start <= current_end:
            current_end = max(current_end, end)
        else:
            covered += current_end - current_start
            current_start, current_end = start, end
    if current_start is not None and current_end is not None:
        covered += current_end - current_start
    return covered / candidate_duration >= BATCH_COVERAGE_THRESHOLD


async def _participant_probe(page: Any) -> None:
    await asyncio.sleep(30)
    try:
        await join_flow.log_participant_signals(page)
    except Exception as err:
        logger.warning("probe.participants_failed", error=type(err).__name__)


async def _advance(bot_id: str, context: RunContext, status: BotStatus) -> None:
    async with session_scope(context.session_factory) as session:
        await _service(session, context).advance(bot_id, status)


async def _summarize(bot_id: str, context: RunContext) -> None:
    if context.summarizer is None:
        await _set_artifact_status(context, bot_id, "notes_status", "disabled")
        return
    async with session_scope(context.session_factory) as session:
        transcript = await _service(session, context).transcript(bot_id)
    if not transcript:
        await _set_artifact_status(context, bot_id, "notes_status", "silence")
        return
    segments = [
        Segment(text=row.text, start_ms=row.start_ms, end_ms=row.end_ms, speaker=row.speaker)
        for row in transcript
    ]
    notes = None
    for attempt in range(SUMMARY_ATTEMPTS):
        try:
            notes = await asyncio.wait_for(
                summarize_segments(context.summarizer.summarize, segments), SUMMARY_TIMEOUT_S
            )
            break
        except Exception as err:
            logger.warning(
                "notes.attempt_failed", bot_id=bot_id, attempt=attempt + 1, error=type(err).__name__
            )
    if notes is None:
        logger.error("notes.exhausted", bot_id=bot_id)
        await _set_artifact_status(context, bot_id, "notes_status", "provider-failed", "exhausted")
        return
    async with session_scope(context.session_factory) as session:
        await _service(session, context).store_notes(bot_id, notes)
    await _set_artifact_status(context, bot_id, "notes_status", "ready")


async def _complete(
    bot_id: str,
    context: RunContext,
    recording: bytes,
    cancelled: bool,
    checkpoint_uri: str | None = None,
    checkpoint_failed: bool = False,
    stop_reason: str | None = None,
) -> None:
    async with session_scope(context.session_factory) as session:
        service = _service(session, context)
        run = await service.get(bot_id)
        if recording:
            run.recording_sha256 = hashlib.sha256(recording).hexdigest()
            run.recording_bytes = len(recording)
            run.recording_expires_at = utcnow() + timedelta(days=context.settings.retention_days)
            run.recording_status = (
                "degraded" if checkpoint_failed else ("partial" if cancelled else "ready")
            )
            run.recording_partial = cancelled or checkpoint_failed
            if context.blob is not None:
                run.recording_uri = await context.blob.put(
                    f"recordings/{bot_id}.webm", recording, "audio/webm"
                )
        elif run.recording_status == "pending":
            run.recording_status = "no-audio"
        if checkpoint_uri is not None:
            run.checkpoint_manifest_uri = checkpoint_uri
        run.finalization_status = "provider-failed" if checkpoint_failed else "ready"
        if cancelled:
            run.error_code = "cancelled"
            run.stop_reason = stop_reason or "cancelled"
        await service.advance(bot_id, BotStatus.COMPLETED)
        if run.joined_at is not None and run.left_at is not None:
            run.recording_duration_ms = int((run.left_at - run.joined_at).total_seconds() * 1000)


async def _persist_recording(
    bot_id: str,
    context: RunContext,
    recording: bytes,
    checkpoint_uri: str | None,
    checkpoint_failed: bool,
    reason: str,
) -> None:
    uri = None
    if recording and context.blob is not None:
        try:
            uri = await context.blob.put(f"recordings/{bot_id}.webm", recording, "audio/webm")
        except Exception as err:
            logger.warning("recording.upload_failed", bot_id=bot_id, error=type(err).__name__)
            checkpoint_failed = True
    elif recording:
        checkpoint_failed = True
    status = (
        "degraded"
        if checkpoint_failed
        else ("partial" if reason == join_flow.END_CANCELLED else "ready")
    )
    async with session_scope(context.session_factory) as session:
        service = _service(session, context)
        if recording:
            await service.store_recording(
                bot_id,
                RecordingArtifact(
                    uri=uri,
                    sha256=hashlib.sha256(recording).hexdigest(),
                    size=len(recording),
                    status=status,
                    partial=reason == join_flow.END_CANCELLED or checkpoint_failed,
                    expires_at=utcnow() + timedelta(days=context.settings.retention_days),
                    checkpoint_manifest_uri=checkpoint_uri,
                ),
            )
        await service.advance(bot_id, BotStatus.PROCESSING)


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
        logger.warning("gate.upload_failed", bot_id=bot_id, error=type(err).__name__)
        return
    logger.info("gate.captured", bot_id=bot_id, uri=uri)


async def fail_run(bot_id: str, context: RunContext, code: str) -> None:
    await _recover_checkpoint(bot_id, context)
    async with session_scope(context.session_factory) as session:
        service = _service(session, context)
        run = await service.get(bot_id)
        if BotStatus(run.status) in (BotStatus.COMPLETED, BotStatus.FAILED):
            return
        run.error_code = code
        run.stop_reason = {
            "cancelled": "cancelled",
            "shutdown": "shutdown",
        }.get(code, "failure")
        await service.advance(bot_id, BotStatus.FAILED)


async def _recover_checkpoint(bot_id: str, context: RunContext) -> None:
    if context.blob is None:
        return
    async with session_scope(context.session_factory) as session:
        run = await _service(session, context).get(bot_id)
        manifest_uri = run.checkpoint_manifest_uri
        if not manifest_uri or BotStatus(run.status) not in {
            BotStatus.JOINED,
            BotStatus.RECORDING,
            BotStatus.PROCESSING,
        }:
            return
    try:
        recording, _, checksum = await restore_prefix(context.blob, manifest_uri)
        if not recording:
            return
        uri = await context.blob.put(f"recordings/{bot_id}.webm", recording, "audio/webm")
    except Exception as err:
        logger.warning("recording.recovery_failed", bot_id=bot_id, error=type(err).__name__)
        return
    async with session_scope(context.session_factory) as session:
        service = _service(session, context)
        run = await service.store_recording(
            bot_id,
            RecordingArtifact(
                uri=uri,
                sha256=checksum,
                size=len(recording),
                status="partial",
                partial=True,
                expires_at=utcnow() + timedelta(days=context.settings.retention_days),
                checkpoint_manifest_uri=manifest_uri,
            ),
        )
        run.finalization_status = "recovered"


async def _requested_stop_reason(context: RunContext, bot_id: str) -> str | None:
    if SHUTDOWN_EVENT.is_set():
        return "shutdown"
    if context.redis is not None and await is_cancelled(context.redis, bot_id):
        return "cancelled"
    return None


def _should_stop(context: RunContext, bot_id: str):
    async def check() -> bool:
        if SHUTDOWN_EVENT.is_set():
            return True
        if context.redis is None:
            return False
        return await is_cancelled(context.redis, bot_id)

    return check


SHUTDOWN_EVENT: asyncio.Event = asyncio.Event()


def _on_sigterm() -> None:
    SHUTDOWN_EVENT.set()


def handle_sigterm() -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.add_signal_handler(signal.SIGTERM, _on_sigterm)


def _missing_blob_key(err: Exception) -> bool:
    return getattr(err, "code", "") == "NoSuchKey"


async def _restore_profile(context: RunContext, tier: str, profile_dir: Path) -> None:
    if context.blob is None or tier == TIER_GUEST:
        logger.warning("profile.restore_skipped", reason="no-blob")
        return
    key = profile_key(context.settings.tenant, PLATFORM_MEET, tier)
    try:
        data = await context.blob.get(key)
    except Exception as err:
        if _missing_blob_key(err):
            return
        logger.warning("profile.restore_failed", error=type(err).__name__)
        return
    unpack_profile(unseal(data, context.settings.profile_encryption_key or None), profile_dir)


async def _persist_profile(context: RunContext, tier: str, profile_dir: Path) -> None:
    if tier == TIER_GUEST:
        shutil.rmtree(profile_dir, ignore_errors=True)
        return
    if context.blob is None:
        return
    key = profile_key(context.settings.tenant, PLATFORM_MEET, tier)
    try:
        sealed = seal(pack_profile(profile_dir), context.settings.profile_encryption_key or None)
        await context.blob.put(key, sealed, PROFILE_CONTENT_TYPE)
    except Exception as err:
        logger.warning("profile.persist_failed", error=type(err).__name__)


async def _beat_loop(redis: Redis, bot_id: str) -> None:
    while True:
        try:
            await beat(redis, bot_id)
        except Exception as err:
            logger.warning("run.heartbeat_failed", bot_id=bot_id, error=type(err).__name__)
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)
