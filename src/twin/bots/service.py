import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from twin.bots.models import BotRun, MeetingNote, TranscriptSegment, WebhookSubscription
from twin.bots.state import BotStatus, require_transition
from twin.core.errors import make_error
from twin.core.time import utcnow
from twin.notes.summarizer import MeetingNotes
from twin.transcription.transcriber import Segment
from twin.webhooks.dispatch import EventSink


class BotRepository(Protocol):
    async def add(self, run: BotRun) -> None: ...

    async def get(self, bot_id: str) -> BotRun | None: ...

    async def segments(self, bot_id: str) -> list[TranscriptSegment]: ...

    async def add_segment(self, segment: TranscriptSegment) -> None: ...

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
        await self._session.commit()

    async def get(self, bot_id: str) -> BotRun | None:
        return await self._session.get(BotRun, bot_id)

    async def segments(self, bot_id: str) -> list[TranscriptSegment]:
        rows = await self._session.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.bot_run_id == bot_id)
            .order_by(TranscriptSegment.start_ms)
        )
        return list(rows.scalars())

    async def add_segment(self, segment: TranscriptSegment) -> None:
        self._session.add(segment)
        await self._session.commit()

    async def save_notes(self, note: MeetingNote) -> None:
        self._session.add(note)
        await self._session.commit()

    async def get_notes(self, bot_id: str) -> MeetingNote | None:
        return await self._session.get(MeetingNote, bot_id)

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
        await self._session.commit()
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
        await self._session.commit()

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
        await self._session.commit()
        return subscription


class SubscriptionService:
    def __init__(
        self, repo: SubscriptionRepository, clock: Callable[[], datetime] = utcnow
    ) -> None:
        self._repo = repo
        self._clock = clock

    async def subscribe(self, url: str) -> WebhookSubscription:
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
        run = BotRun(
            id=f"bot_{uuid.uuid4().hex}",
            meeting_url=meeting_url,
            display_name=display_name,
            status=BotStatus.QUEUED.value,
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        await self._repo.add(run)
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

    async def advance(self, bot_id: str, target: BotStatus) -> BotRun:
        run = await self.get(bot_id)
        require_transition(BotStatus(run.status), target)
        run.status = target.value
        now = self._clock()
        if target is BotStatus.JOINED and run.joined_at is None:
            run.joined_at = now
        if target in (BotStatus.COMPLETED, BotStatus.FAILED):
            run.left_at = now
        await self._repo.add(run)
        if self._events is not None:
            await self._events.status_changed(bot_id, target.value)
        return run
