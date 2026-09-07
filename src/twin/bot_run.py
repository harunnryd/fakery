import asyncio
import sys

import structlog
from redis.asyncio import from_url

from twin.bots.runtime import RunContext, _prepare_launch, _service, attend, fail_run
from twin.core.config import get_settings
from twin.meet import join_flow
from twin.storage.blob import MinioBlobStore
from twin.storage.database import create_engine_and_sessionmaker
from twin.storage.profiles import validate_key
from twin.transcription.transcriber import launch_transcriber
from twin.webhooks.dispatch import WebhookDispatcher

logger = structlog.get_logger(__name__)


async def _run(bot_id: str) -> int:
    settings = get_settings()
    if settings.profile_encryption_key:
        validate_key(settings.profile_encryption_key)
    engine, session_factory = create_engine_and_sessionmaker(settings.database_url)
    redis = from_url(settings.redis_url, decode_responses=True)
    blob = MinioBlobStore(
        settings.blob_endpoint,
        settings.blob_access_key,
        settings.blob_secret_key,
        settings.blob_bucket,
    )
    context = RunContext(
        session_factory=session_factory,
        settings=settings,
        blob=blob,
        redis=redis,
        owner=f"job-{bot_id}",
        transcriber=(
            launch_transcriber(settings.stt_provider, settings.stt_api_key, settings.stt_diarize)
            if settings.stt_api_key
            else None
        ),
        events=WebhookDispatcher(session_factory, settings.webhook_signing_secret),
    )
    try:
        launch = _prepare_launch(settings)
        async with context.session_factory() as session:
            run = await _service(session).get(bot_id)
        display = run.display_name or settings.bot_display_name
        await attend(bot_id, run.meeting_url, display, context, launch)
    except join_flow.JoinError as err:
        await fail_run(bot_id, context, err.code)
        return 0
    except Exception as err:
        logger.error("bot_run.failed", bot_id=bot_id, error=str(err))
        try:
            await fail_run(bot_id, context, "internal")
        except Exception:
            pass
        return 1
    finally:
        await redis.aclose()
        await engine.dispose()
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m twin.bot_run BOT_ID", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_run(sys.argv[1])))


if __name__ == "__main__":
    main()
