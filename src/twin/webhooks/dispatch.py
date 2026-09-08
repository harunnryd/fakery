import asyncio
import json
from typing import Protocol

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from twin.bots.models import TranscriptSegment, WebhookSubscription
from twin.core.time import utcnow
from twin.storage.database import session_scope
from twin.webhooks.signing import SIGNATURE_HEADER, sign

logger = structlog.get_logger(__name__)

TIMESTAMP_HEADER = "X-Webhook-Timestamp"
SEND_ATTEMPTS = 3
RETRY_BASE_S = 1
SEND_TIMEOUT_S = 10


class EventSink(Protocol):
    async def status_changed(self, bot_id: str, status: str) -> None: ...

    async def transcript_segment(self, segment: TranscriptSegment) -> None: ...


def status_payload(bot_id: str, status: str) -> dict:
    return {"type": "bot.status_changed", "bot_id": bot_id, "status": status}


def segment_payload(segment: TranscriptSegment) -> dict:
    return {
        "type": "transcript.segment",
        "bot_id": segment.bot_run_id,
        "segment": {
            "id": segment.id,
            "speaker": segment.speaker,
            "text": segment.text,
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
        },
    }


def notes_payload(bot_id: str) -> dict:
    return {"type": "notes.completed", "bot_id": bot_id}


class WebhookDispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._secret = secret
        self._client = client

    async def status_changed(self, bot_id: str, status: str) -> None:
        await self._fanout(status_payload(bot_id, status))

    async def transcript_segment(self, segment: TranscriptSegment) -> None:
        await self._fanout(segment_payload(segment))

    async def notes_completed(self, bot_id: str) -> None:
        await self._fanout(notes_payload(bot_id))

    async def _fanout(self, payload: dict) -> None:
        async with session_scope(self._session_factory) as session:
            rows = await session.execute(select(WebhookSubscription))
            urls = [row.url for row in list(rows.scalars())]
        for url in urls:
            await self._deliver(url, payload)

    async def _deliver(self, url: str, payload: dict) -> None:
        body = json.dumps(payload, sort_keys=True).encode()
        timestamp = utcnow().isoformat()
        headers = {
            "content-type": "application/json",
            SIGNATURE_HEADER: sign(self._secret, body, timestamp),
            TIMESTAMP_HEADER: timestamp,
        }
        client = self._client or httpx.AsyncClient()
        try:
            for attempt in range(SEND_ATTEMPTS):
                try:
                    response = await client.post(
                        url, content=body, headers=headers, timeout=SEND_TIMEOUT_S
                    )
                    response.raise_for_status()
                    return
                except Exception as err:
                    logger.warning(
                        "webhook.delivery_failed",
                        url=url,
                        attempt=attempt + 1,
                        error=str(err),
                    )
                    await asyncio.sleep(RETRY_BASE_S * 2**attempt)
            logger.error("webhook.delivery_exhausted", url=url, event_type=payload.get("type"))
        finally:
            if self._client is None:
                await client.aclose()
