import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from twin.bots.models import BotRun, TranscriptSegment
from twin.bots.state import BotStatus, require_transition
from twin.core.errors import make_error
from twin.core.time import utcnow


class BotRepository(Protocol):
    async def add(self, run: BotRun) -> None: ...

    async def get(self, bot_id: str) -> BotRun | None: ...

    async def segments(self, bot_id: str) -> list[TranscriptSegment]: ...


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


class BotService:
    def __init__(self, repo: BotRepository, clock: Callable[[], datetime] = utcnow) -> None:
        self._repo = repo
        self._clock = clock

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
        return run
