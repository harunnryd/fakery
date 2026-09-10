import asyncio
import signal

import structlog
from redis.asyncio import from_url

from twin.core.config import get_settings
from twin.storage.database import create_engine_and_sessionmaker
from twin.webhooks.dispatch import OUTBOX_POLL_S, WebhookSender

logger = structlog.get_logger(__name__)
HEARTBEAT_KEY = "workers:heartbeat:sender"
HEARTBEAT_TTL_S = 60


async def main() -> None:
    settings = get_settings()
    engine, session_factory = create_engine_and_sessionmaker(settings.database_url)
    redis = from_url(settings.redis_url, decode_responses=True)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopping.set)
    loop.add_signal_handler(signal.SIGINT, stopping.set)
    sender = WebhookSender(session_factory, settings.webhook_signing_secret)
    logger.info("sender.started")
    try:
        while not stopping.is_set():
            try:
                await redis.set(HEARTBEAT_KEY, "1", ex=HEARTBEAT_TTL_S)
                summary = await sender.run_once(redis)
                await redis.set(HEARTBEAT_KEY, "1", ex=HEARTBEAT_TTL_S)
                logger.info("sender.sweep", **summary)
            except Exception as err:
                logger.warning("sender.sweep_failed", error=type(err).__name__)
            try:
                await asyncio.wait_for(stopping.wait(), timeout=OUTBOX_POLL_S)
            except TimeoutError:
                continue
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
