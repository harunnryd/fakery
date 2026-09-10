import re
import socket
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from ipaddress import ip_address
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from twin.bots.models import BotRun, MeetingNote, TranscriptSegment, WebhookSubscription
from twin.bots.state import LIVE_STATUSES, BotStatus, require_transition
from twin.core.errors import make_error
from twin.core.time import utcnow
from twin.notes.summarizer import MeetingNotes
from twin.transcription.transcriber import Segment
from twin.webhooks.dispatch import EventSink


@dataclass(frozen=True, slots=True)
class RecordingArtifact:
    uri: str | None
    sha256: str
    size: int
    status: str
    partial: bool
    expires_at: datetime
    checkpoint_manifest_uri: str | None


async def _flush(session: AsyncSession) -> None:
    flush = getattr(session, "flush", None)
    if flush is not None:
        await flush()


class BotRepository(Protocol):
    async def add(self, run: BotRun) -> None: ...

    async def get(self, bot_id: str) -> BotRun | None: ...

    async def get_for_update(self, bot_id: str) -> BotRun | None: ...

    async def get_by_idempotency(self, idempotency_key: str) -> BotRun | None: ...

    async def find_active_meeting(self, meeting_key: str) -> BotRun | None: ...

    async def segments(self, bot_id: str) -> list[TranscriptSegment]: ...

    async def add_segment(self, segment: TranscriptSegment) -> None: ...

    async def find_segment(self, bot_id: str, segment: Segment) -> TranscriptSegment | None: ...

    async def list_runs(self, limit: int, cursor: str | None) -> list[BotRun]: ...

    async def annotate_speakers(
        self, bot_id: str, start_ms: int, end_ms: int, speaker: str
    ) -> int: ...

    async def save_notes(self, note: MeetingNote) -> None: ...

    async def get_notes(self, bot_id: str) -> MeetingNote | None: ...


class SqlalchemyBotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: BotRun) -> None:
        self._session.add(run)
        await _flush(self._session)

    async def get(self, bot_id: str) -> BotRun | None:
        return await self._session.get(BotRun, bot_id)

    async def get_for_update(self, bot_id: str) -> BotRun | None:
        if not hasattr(self._session, "bind"):
            return await self.get(bot_id)
        row = await self._session.execute(
            select(BotRun).where(BotRun.id == bot_id).with_for_update()
        )
        return row.scalar_one_or_none()

    async def get_by_idempotency(self, idempotency_key: str) -> BotRun | None:
        row = await self._session.execute(
            select(BotRun).where(BotRun.idempotency_key == idempotency_key)
        )
        return row.scalar_one_or_none()

    async def find_active_meeting(self, meeting_key: str) -> BotRun | None:
        row = await self._session.execute(
            select(BotRun)
            .where(
                BotRun.meeting_key == meeting_key,
                BotRun.status.in_([status.value for status in LIVE_STATUSES | {BotStatus.QUEUED}]),
            )
            .order_by(BotRun.created_at)
            .limit(1)
        )
        return row.scalar_one_or_none()

    async def segments(self, bot_id: str) -> list[TranscriptSegment]:
        rows = await self._session.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.bot_run_id == bot_id)
            .order_by(TranscriptSegment.start_ms)
        )
        return list(rows.scalars())

    async def add_segment(self, segment: TranscriptSegment) -> None:
        self._session.add(segment)
        await _flush(self._session)

    async def find_segment(self, bot_id: str, segment: Segment) -> TranscriptSegment | None:
        row = await self._session.execute(
            select(TranscriptSegment).where(
                TranscriptSegment.bot_run_id == bot_id,
                TranscriptSegment.start_ms == segment.start_ms,
                TranscriptSegment.end_ms == segment.end_ms,
                TranscriptSegment.text == segment.text,
            )
        )
        return row.scalar_one_or_none()

    async def save_notes(self, note: MeetingNote) -> None:
        self._session.add(note)
        await _flush(self._session)

    async def get_notes(self, bot_id: str) -> MeetingNote | None:
        return await self._session.get(MeetingNote, bot_id)

    async def list_runs(self, limit: int, cursor: str | None) -> list[BotRun]:
        stmt = select(BotRun).order_by(BotRun.created_at.desc(), BotRun.id.desc()).limit(limit)
        if cursor is not None:
            anchor = await self._session.get(BotRun, cursor)
            if anchor is not None:
                stmt = stmt.where(
                    tuple_(BotRun.created_at, BotRun.id) < (anchor.created_at, anchor.id)
                )
        rows = await self._session.execute(stmt)
        return list(rows.scalars())

    async def annotate_speakers(self, bot_id: str, start_ms: int, end_ms: int, speaker: str) -> int:
        rows = await self._session.execute(
            select(TranscriptSegment).where(
                TranscriptSegment.bot_run_id == bot_id,
                TranscriptSegment.speaker.is_(None),
                TranscriptSegment.start_ms < end_ms,
                TranscriptSegment.end_ms > start_ms,
            )
        )
        count = 0
        for segment in list(rows.scalars()):
            segment.speaker = speaker
            count += 1
        await _flush(self._session)
        return count


class SubscriptionRepository(Protocol):
    async def add(self, subscription: WebhookSubscription) -> None: ...

    async def list(self) -> list[WebhookSubscription]: ...

    async def remove(self, subscription_id: str) -> WebhookSubscription | None: ...


class SqlalchemySubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, subscription: WebhookSubscription) -> None:
        self._session.add(subscription)
        await _flush(self._session)

    async def list(self) -> list[WebhookSubscription]:
        rows = await self._session.execute(
            select(WebhookSubscription).order_by(WebhookSubscription.created_at)
        )
        return list(rows.scalars())

    async def remove(self, subscription_id: str) -> WebhookSubscription | None:
        subscription = await self._session.get(WebhookSubscription, subscription_id)
        if subscription is None:
            return None
        await self._session.delete(subscription)
        await _flush(self._session)
        return subscription


class SubscriptionService:
    def __init__(
        self, repo: SubscriptionRepository, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self._repo = repo
        self._clock = clock

    async def subscribe(self, url: str) -> WebhookSubscription:
        validate_webhook_url(url)
        subscription = WebhookSubscription(
            id=f"wh_{uuid.uuid4().hex}",
            url=url,
            created_at=self._clock(),
        )
        await self._repo.add(subscription)
        return subscription

    async def subscriptions(self) -> list[WebhookSubscription]:
        return await self._repo.list()

    async def unsubscribe(self, subscription_id: str) -> None:
        removed = await self._repo.remove(subscription_id)
        if removed is None:
            raise make_error("not-found", detail=f"webhook {subscription_id} does not exist")


class BotService:
    def __init__(
        self,
        repo: BotRepository,
        clock: Callable[[], datetime] = utcnow,
        events: EventSink | None = None,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._events = events

    async def create(self, meeting_url: str, display_name: str | None = None) -> BotRun:
        run, _ = await self.create_or_get(meeting_url, display_name)
        return run

    async def create_or_get(
        self,
        meeting_url: str,
        display_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[BotRun, bool]:
        canonical_url, meeting_key = normalize_meeting_url(meeting_url)
        if idempotency_key:
            existing = await self._repo.get_by_idempotency(idempotency_key)
            if existing is not None:
                existing_key = (
                    existing.meeting_key or normalize_meeting_url(existing.meeting_url)[1]
                )
                if existing_key != meeting_key:
                    raise make_error(
                        "conflict", detail="Idempotency-Key was already used for another meeting"
                    )
                return existing, False
        active = await self._repo.find_active_meeting(meeting_key)
        if active is not None:
            raise make_error("conflict", detail="an active bot already exists for this meeting")
        run = BotRun(
            id=f"bot_{uuid.uuid4().hex}",
            meeting_url=canonical_url,
            meeting_key=meeting_key,
            idempotency_key=idempotency_key,
            display_name=display_name,
            status=BotStatus.QUEUED.value,
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        try:
            await self._repo.add(run)
        except IntegrityError as err:
            raise make_error(
                "conflict", detail="an active bot already exists for this meeting"
            ) from err
        if self._events is not None:
            await self._events.status_changed(run.id, BotStatus.QUEUED.value)
        return run, True

    async def cancel(self, bot_id: str) -> BotRun:
        run = await self.get(bot_id)
        status = BotStatus(run.status)
        if status in (BotStatus.COMPLETED, BotStatus.FAILED):
            return run
        run.error_code = "cancelled"
        run.stop_reason = "cancelled"
        await self.advance(bot_id, BotStatus.FAILED)
        return run

    async def get(self, bot_id: str) -> BotRun:
        run = await self._repo.get(bot_id)
        if run is None:
            raise make_error("not-found", detail=f"bot {bot_id} does not exist")
        return run

    async def transcript(self, bot_id: str) -> list[TranscriptSegment]:
        await self.get(bot_id)
        return await self._repo.segments(bot_id)

    async def ingest_segment(self, bot_id: str, segment: Segment) -> TranscriptSegment:
        existing = getattr(self._repo, "find_segment", None)
        if existing is not None:
            found = await existing(bot_id, segment)
            if found is not None:
                return found
        stored = TranscriptSegment(
            id=f"seg_{uuid.uuid4().hex}",
            bot_run_id=bot_id,
            speaker=segment.speaker,
            text=segment.text,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            created_at=self._clock(),
        )
        await self._repo.add_segment(stored)
        if self._events is not None:
            await self._events.transcript_segment(stored)
        return stored

    async def annotate_speakers(self, bot_id: str, segments: list[Segment]) -> int:
        annotated = 0
        for segment in segments:
            if segment.speaker is None:
                continue
            annotated += await self._repo.annotate_speakers(
                bot_id, segment.start_ms, segment.end_ms, segment.speaker
            )
        return annotated

    async def store_notes(self, bot_id: str, notes: MeetingNotes) -> MeetingNote:
        note = MeetingNote(
            bot_run_id=bot_id,
            summary=notes.summary,
            key_points=list(notes.key_points),
            action_items=[asdict(item) for item in notes.action_items],
            created_at=self._clock(),
        )
        await self._repo.save_notes(note)
        if self._events is not None:
            await self._events.notes_completed(bot_id)
        return note

    async def get_notes(self, bot_id: str) -> MeetingNote:
        note = await self._repo.get_notes(bot_id)
        if note is None:
            raise make_error("not-found", detail=f"notes for {bot_id} do not exist")
        return note

    async def store_recording(self, bot_id: str, artifact: RecordingArtifact) -> BotRun:
        run = await self.get(bot_id)
        run.recording_uri = artifact.uri
        run.recording_sha256 = artifact.sha256
        run.recording_bytes = artifact.size
        run.recording_status = artifact.status
        run.recording_partial = artifact.partial
        run.recording_expires_at = artifact.expires_at
        run.checkpoint_manifest_uri = artifact.checkpoint_manifest_uri
        await self._repo.add(run)
        return run

    async def set_checkpoint_manifest(self, bot_id: str, manifest_uri: str) -> BotRun:
        run = await self.get(bot_id)
        run.checkpoint_manifest_uri = manifest_uri
        run.finalization_status = "checkpointed"
        await self._repo.add(run)
        return run

    async def set_artifact_status(
        self, bot_id: str, artifact: str, status: str, error: str | None = None
    ) -> BotRun:
        fields = {
            "transcription": ("transcription_status", "transcription_error"),
            "notes": ("notes_status", "notes_error"),
        }
        try:
            status_field, error_field = fields[artifact]
        except KeyError:
            raise ValueError(f"unknown artifact: {artifact}") from None
        run = await self.get(bot_id)
        setattr(run, status_field, status)
        setattr(run, error_field, error)
        await self._repo.add(run)
        return run

    async def list_runs(self, limit: int, cursor: str | None) -> tuple[list[BotRun], str | None]:
        rows = await self._repo.list_runs(limit + 1, cursor)
        if len(rows) <= limit:
            return rows, None
        return rows[:limit], rows[limit - 1].id

    async def advance(self, bot_id: str, target: BotStatus) -> BotRun:
        locked = getattr(self._repo, "get_for_update", None)
        run = await locked(bot_id) if locked is not None else await self.get(bot_id)
        if run is None:
            raise make_error("not-found", detail=f"bot {bot_id} does not exist")
        current = BotStatus(run.status)
        if current in (BotStatus.COMPLETED, BotStatus.FAILED):
            if current is target:
                return run
            raise make_error("conflict", detail=f"terminal bot {bot_id} cannot transition")
        require_transition(current, target)
        run.status = target.value
        now = self._clock()
        if target is BotStatus.JOINED and run.joined_at is None:
            run.joined_at = now
        if target in (BotStatus.COMPLETED, BotStatus.FAILED):
            run.left_at = now
            if target is BotStatus.FAILED and run.stop_reason is None:
                run.stop_reason = "failure"
        await self._repo.add(run)
        if self._events is not None:
            await self._events.status_changed(bot_id, target.value)
        return run


def normalize_meeting_url(meeting_url: str) -> tuple[str, str]:
    parsed = urlsplit(meeting_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.rstrip("/")
    parts = [part for part in path.split("/") if part]
    if parsed.scheme.lower() != "https" or host != "meet.google.com" or len(parts) != 1:
        raise make_error("validation-failed", detail="meeting_url must be a Google Meet HTTPS URL")
    code = parts[0].lower()
    if re.fullmatch(r"[a-z0-9]{3}-[a-z0-9]{4,}-[a-z0-9]{3}", code) is None:
        raise make_error("validation-failed", detail="meeting_url has an invalid meeting code")
    canonical = urlunsplit(("https", "meet.google.com", f"/{code}", "", ""))
    return canonical, f"meet.google.com/{code}"


def validate_webhook_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host:
        raise make_error("validation-failed", detail="webhook URL must use HTTPS")
    if host in {"localhost", "metadata.google.internal", "metadata"} or host.endswith(
        (".local", ".internal")
    ):
        raise make_error("validation-failed", detail="webhook URL targets a blocked host")
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
        raise make_error("validation-failed", detail="webhook URL targets a blocked network")
    if address is None:
        try:
            resolved = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError:
            resolved = []
        for _, _, _, _, sockaddr in resolved:
            resolved_address = ip_address(sockaddr[0])
            if (
                resolved_address.is_private
                or resolved_address.is_loopback
                or resolved_address.is_link_local
            ):
                raise make_error(
                    "validation-failed", detail="webhook URL targets a blocked network"
                )
    return url
