import json
import random
import uuid
from datetime import datetime, timedelta
from typing import Protocol

import httpx
import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from twin.bots.models import TranscriptSegment, WebhookDelivery, WebhookSubscription
from twin.bots.queue import acquire_lease, release_lease
from twin.core.metrics import increment
from twin.core.time import utcnow
from twin.storage.database import session_scope
from twin.webhooks.signing import SIGNATURE_HEADER, sign

logger = structlog.get_logger(__name__)

TIMESTAMP_HEADER = "X-Webhook-Timestamp"
SEND_TIMEOUT_S = 10
RETRY_BASE_S = 2
RETRY_CAP_S = 300
MAX_ATTEMPTS = 10
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
    def __init__(self, session_factory: async_sessionmaker | AsyncSession) -> None:
        self._session_source = session_factory

    async def status_changed(self, bot_id: str, status: str) -> None:
        await self._store(status_payload(bot_id, status, new_event_id()))

    async def transcript_segment(self, segment: TranscriptSegment) -> None:
        await self._store(segment_payload(segment, new_event_id()))

    async def notes_completed(self, bot_id: str) -> None:
        await self._store(notes_payload(bot_id, new_event_id()))

    async def _store(self, payload: dict) -> None:
        if isinstance(self._session_source, AsyncSession):
            await self._store_in_session(self._session_source, payload)
            return
        async with session_scope(self._session_source) as session:
            await self._store_in_session(session, payload)

    async def _store_in_session(self, session: AsyncSession, payload: dict) -> None:
        rows = await session.execute(select(WebhookSubscription).with_for_update())
        subscriptions = list(rows.scalars())
        now = utcnow()
        event_id = str(payload["event_id"])
        for subscription in subscriptions:
            subscription_id = getattr(subscription, "id", None) or f"sub_{subscription.url}"
            existing = await session.execute(
                select(WebhookDelivery)
                .where(
                    WebhookDelivery.subscription_id == subscription_id,
                    WebhookDelivery.payload["bot_id"].as_string() == str(payload["bot_id"]),
                )
                .order_by(WebhookDelivery.sequence.desc())
                .limit(1)
            )
            previous = next(iter(existing.scalars()), None)
            sequence = int(getattr(previous, "sequence", 0) or 0) + 1
            session.add(
                WebhookDelivery(
                    id=f"whd_{uuid.uuid4().hex}",
                    subscription_id=subscription_id,
                    event_id=event_id,
                    sequence=sequence,
                    url=subscription.url,
                    payload=payload,
                    status="pending",
                    attempts=0,
                    next_try_at=now,
                    created_at=now,
                )
            )


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
                .where(
                    WebhookDelivery.status.in_(("pending", "retry", "dead")),
                )
                .order_by(WebhookDelivery.sequence, WebhookDelivery.next_try_at, WebhookDelivery.id)
                .limit(SEND_BATCH)
            )
            pending = _ready_deliveries(list(rows.scalars()), moment)
        for (bot_id, subscription_id), items in _group_by_stream(pending):
            lease_key = _send_lease(bot_id, subscription_id)
            if not await acquire_lease(redis, lease_key, "sender", SEND_LEASE_TTL_S):
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
                await release_lease(redis, lease_key, "sender")
        purged = await self._purge(moment)
        return {"sent": sent, "failed": failed, "dead": dead, "purged": purged}

    async def _attempt(self, item: WebhookDelivery) -> str:
        token = f"lease_{uuid.uuid4().hex}"
        async with session_scope(self._session_factory) as session:
            row = await session.execute(
                select(WebhookDelivery).where(WebhookDelivery.id == item.id).with_for_update()
            )
            scalar_one_or_none = getattr(row, "scalar_one_or_none", None)
            fresh = (
                scalar_one_or_none()
                if scalar_one_or_none is not None
                else next(iter(row.scalars()), None)
            )
            if fresh is None:
                return "failed"
            status = getattr(fresh, "status", None) or "pending"
            if status not in {"pending", "retry"}:
                return "failed"
            lease_expires_at = getattr(fresh, "lease_expires_at", None)
            if lease_expires_at is not None and lease_expires_at > utcnow():
                return "failed"
            fresh.lease_token = token
            fresh.lease_expires_at = utcnow() + timedelta(seconds=SEND_LEASE_TTL_S)
            await session.commit()
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
                if response.status_code >= 300:
                    return await self._record_failure(
                        item,
                        token,
                        httpx.HTTPStatusError(
                            f"webhook returned {response.status_code}",
                            request=response.request,
                            response=response,
                        ),
                    )
            except Exception as err:
                return await self._record_failure(item, token, err)
            async with session_scope(self._session_factory) as session:
                fresh = await session.get(WebhookDelivery, item.id)
                if fresh is not None and fresh.lease_token == token:
                    fresh.status = "sent"
                    fresh.sent_at = utcnow()
                    fresh.lease_token = None
                    fresh.lease_expires_at = None
                    await session.commit()
                    increment("fakery_webhook_sent_total")
            return "sent"
        finally:
            if self._client is None:
                await client.aclose()

    async def _record_failure(self, item: WebhookDelivery, token: str, err: Exception) -> str:
        logger.warning(
            "webhook.delivery_failed", attempt=item.attempts + 1, error=type(err).__name__
        )
        async with session_scope(self._session_factory) as session:
            fresh = await session.get(WebhookDelivery, item.id)
            if fresh is None:
                return "failed"
            if fresh.lease_token != token:
                return "failed"
            fresh.attempts = int(fresh.attempts or 0) + 1
            response = getattr(err, "response", None)
            fresh.last_status_code = getattr(response, "status_code", None)
            fresh.last_error = type(err).__name__
            if not _is_retryable(response) or fresh.attempts >= MAX_ATTEMPTS:
                logger.error(
                    "webhook.delivery_dead",
                    event_type=(item.payload or {}).get("type"),
                )
                fresh.status = "dead"
                fresh.dead_at = utcnow()
                fresh.lease_token = None
                fresh.lease_expires_at = None
                await session.commit()
                increment("fakery_webhook_dead_total")
                return "dead"
            retry_after = _retry_after_seconds(response)
            backoff = min(RETRY_CAP_S, RETRY_BASE_S * 2 ** (fresh.attempts - 1))
            backoff = max(backoff, retry_after or 0)
            backoff += random.uniform(0, min(1.0, backoff * 0.1))
            fresh.status = "retry"
            fresh.next_try_at = utcnow() + timedelta(seconds=backoff)
            fresh.lease_token = None
            fresh.lease_expires_at = None
            await session.commit()
            increment("fakery_webhook_retry_total")
            return "failed"

    async def _purge(self, moment: datetime) -> int:
        cutoff = moment - timedelta(days=ROW_TTL_DAYS)
        async with session_scope(self._session_factory) as session:
            rows = await session.execute(
                select(WebhookDelivery).where(
                    WebhookDelivery.created_at < cutoff,
                    WebhookDelivery.status.in_(("sent", "dead", "skipped")),
                )
            )
            count = 0
            for row in list(rows.scalars()):
                await session.delete(row)
                count += 1
            await session.commit()
            return count


def _send_lease(bot_id: str, subscription_id: str | None = None) -> str:
    stream = bot_id if subscription_id is None else f"{bot_id}:{subscription_id}"
    return f"webhooks:send:{stream}"


def _group_by_stream(
    items: list[WebhookDelivery],
) -> list[tuple[tuple[str, str | None], list[WebhookDelivery]]]:
    groups: dict[tuple[str, str | None], list[WebhookDelivery]] = {}
    for item in items:
        payload = item.payload or {}
        key = (str(payload.get("bot_id", "")), getattr(item, "subscription_id", None))
        groups.setdefault(key, []).append(item)
    for group in groups.values():
        group.sort(key=lambda item: (getattr(item, "sequence", 0), item.id))
    return list(groups.items())


def _ready_deliveries(items: list[WebhookDelivery], moment: datetime) -> list[WebhookDelivery]:
    groups = _group_by_stream(items)
    ready: list[WebhookDelivery] = []
    for _, group in groups:
        status = getattr(group[0], "status", None) or "pending"
        due = getattr(group[0], "next_try_at", moment) <= moment
        if group and status in {"pending", "retry"} and due:
            ready.append(group[0])
    return ready


def _retry_after_seconds(response: httpx.Response | None) -> int | None:
    if response is None:
        return None
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None


def _is_retryable(response: httpx.Response | None) -> bool:
    if response is None:
        return True
    return response.status_code in {408, 429} or 500 <= response.status_code <= 599
