import json
import uuid
from datetime import datetime, timedelta
from typing import Protocol

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from twin.bots.models import TranscriptSegment, WebhookDelivery, WebhookSubscription
from twin.bots.queue import acquire_lease, release_lease
from twin.core.time import utcnow
from twin.storage.database import session_scope
from twin.webhooks.signing import SIGNATURE_HEADER, sign

logger = structlog.get_logger(__name__)

TIMESTAMP_HEADER = "X-Webhook-Timestamp"
SEND_TIMEOUT_S = 10
RETRY_BASE_S = 1
MAX_ATTEMPTS = 5
SEND_BATCH = 50
OUTBOX_POLL_S = 10
ROW_TTL_DAYS = 30
SEND_LEASE_TTL_S = 120


class EventSink(Protocol):
    async def status_changed(self, bot_id: str, status: str) -> None: ...

    async def transcript_segment(self, segment: TranscriptSegment) -> None: ...

    async def notes_completed(self, bot_id: str) -> None: ...


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def status_payload(bot_id: str, status: str, event_id: str) -> dict:
    return {
        "type": "bot.status_changed",
        "bot_id": bot_id,
        "status": status,
        "event_id": event_id,
    }


def segment_payload(segment: TranscriptSegment, event_id: str) -> dict:
    return {
        "type": "transcript.segment",
        "bot_id": segment.bot_run_id,
        "event_id": event_id,
        "segment": {
            "id": segment.id,
            "speaker": segment.speaker,
            "text": segment.text,
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
        },
    }


def notes_payload(bot_id: str, event_id: str) -> dict:
    return {"type": "notes.completed", "bot_id": bot_id, "event_id": event_id}


class DbOutbox:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def status_changed(self, bot_id: str, status: str) -> None:
        await self._store(status_payload(bot_id, status, new_event_id()))

    async def transcript_segment(self, segment: TranscriptSegment) -> None:
        await self._store(segment_payload(segment, new_event_id()))

    async def notes_completed(self, bot_id: str) -> None:
        await self._store(notes_payload(bot_id, new_event_id()))

    async def _store(self, payload: dict) -> None:
        async with session_scope(self._session_factory) as session:
            rows = await session.execute(select(WebhookSubscription))
            urls = [row.url for row in list(rows.scalars())]
            now = utcnow()
            for url in urls:
                session.add(
                    WebhookDelivery(
                        id=f"whd_{uuid.uuid4().hex}",
                        url=url,
                        payload=payload,
                        attempts=0,
                        next_try_at=now,
                        created_at=now,
                    )
                )
            await session.commit()


class WebhookSender:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._secret = secret
        self._client = client

    async def run_once(self, redis: Redis) -> dict:
        moment = utcnow()
        sent = failed = dead = 0
        async with session_scope(self._session_factory) as session:
            rows = await session.execute(
                select(WebhookDelivery)
                .where(WebhookDelivery.next_try_at <= moment)
                .order_by(WebhookDelivery.next_try_at, WebhookDelivery.id)
                .limit(SEND_BATCH)
            )
            pending = list(rows.scalars())
        for bot_id, items in _group_by_bot(pending):
            if not await acquire_lease(redis, _send_lease(bot_id), "sender", SEND_LEASE_TTL_S):
                continue
            try:
                for item in items:
                    outcome = await self._attempt(item)
                    if outcome == "sent":
                        sent += 1
                    elif outcome == "dead":
                        dead += 1
                    else:
                        failed += 1
            finally:
                await release_lease(redis, _send_lease(bot_id), "sender")
        purged = await self._purge(moment)
        return {"sent": sent, "failed": failed, "dead": dead, "purged": purged}

    async def _attempt(self, item: WebhookDelivery) -> str:
        body = json.dumps(item.payload, sort_keys=True).encode()
        timestamp = utcnow().isoformat()
        headers = {
            "content-type": "application/json",
            SIGNATURE_HEADER: sign(self._secret, body, timestamp),
            TIMESTAMP_HEADER: timestamp,
        }
        client = self._client or httpx.AsyncClient()
        try:
            try:
                response = await client.post(
                    item.url, content=body, headers=headers, timeout=SEND_TIMEOUT_S
                )
                response.raise_for_status()
            except Exception as err:
                return await self._record_failure(item, err)
            async with session_scope(self._session_factory) as session:
                fresh = await session.get(WebhookDelivery, item.id)
                if fresh is not None:
                    await session.delete(fresh)
                    await session.commit()
            return "sent"
        finally:
            if self._client is None:
                await client.aclose()

    async def _record_failure(self, item: WebhookDelivery, err: Exception) -> str:
        logger.warning(
            "webhook.delivery_failed", url=item.url, attempt=item.attempts + 1, error=str(err)
        )
        async with session_scope(self._session_factory) as session:
            fresh = await session.get(WebhookDelivery, item.id)
            if fresh is None:
                return "failed"
            fresh.attempts += 1
            if fresh.attempts >= MAX_ATTEMPTS:
                logger.error(
                    "webhook.delivery_dead",
                    url=item.url,
                    event_type=item.payload.get("type"),
                )
                await session.delete(fresh)
                await session.commit()
                return "dead"
            backoff = RETRY_BASE_S * 2**fresh.attempts
            fresh.next_try_at = utcnow() + timedelta(seconds=backoff)
            await session.commit()
            return "failed"

    async def _purge(self, moment: datetime) -> int:
        cutoff = moment - timedelta(days=ROW_TTL_DAYS)
        async with session_scope(self._session_factory) as session:
            rows = await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.created_at < cutoff)
            )
            count = 0
            for row in list(rows.scalars()):
                await session.delete(row)
                count += 1
            await session.commit()
            return count


def _send_lease(bot_id: str) -> str:
    return f"webhooks:send:{bot_id}"


def _group_by_bot(items: list[WebhookDelivery]) -> list[tuple[str, list[WebhookDelivery]]]:
    groups: dict[str, list[WebhookDelivery]] = {}
    for item in items:
        key = str(item.payload.get("bot_id", ""))
        groups.setdefault(key, []).append(item)
    return list(groups.items())
