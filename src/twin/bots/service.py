import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from twin.bots.models import BotRun, TranscriptSegment, WebhookSubscription
from twin.bots.state import BotStatus, require_transition
from twin.core.errors import make_error
from twin.core.time import utcnow
from twin.webhooks.dispatch import EventSink


class BotRepository(Protocol):
    async def add(self, run: BotRun) -> None: ...

    async def get(self, bot_id: str) -> BotRun | None: ...

    async def segments(self, bot_id: str) -> list[TranscriptSegment]: ...

    async def add_segment(self, segment: TranscriptSegment) -> None: ...


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

    async def ingest_segment(
        self,
        bot_id: str,
        text: str,
        start_ms: int,
        end_ms: int,
        speaker: str | None = None,
    ) -> TranscriptSegment:
        segment = TranscriptSegment(
            id=f"seg_{uuid.uuid4().hex}",
            bot_run_id=bot_id,
            speaker=speaker,
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            created_at=self._clock(),
        )
        await self._repo.add_segment(segment)
        if self._events is not None:
            await self._events.transcript_segment(segment)
        return segment

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
