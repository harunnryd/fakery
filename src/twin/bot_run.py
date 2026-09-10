import asyncio
import sys
import traceback

import structlog
from redis.asyncio import from_url

from twin.bots.runtime import (
    _prepare_launch,
    _service,
    attend,
    build_context,
    fail_run,
    handle_sigterm,
)
from twin.core.config import get_settings
from twin.meet import join_flow
from twin.storage.blob import MinioBlobStore
from twin.storage.database import create_engine_and_sessionmaker
from twin.storage.profiles import validate_key

logger = structlog.get_logger(__name__)


def _error_location(err: BaseException) -> dict[str, str | int]:
    frames = traceback.extract_tb(err.__traceback__)
    if not frames:
        return {"error_type": type(err).__name__}
    frame = frames[-1]
    return {
        "error_type": type(err).__name__,
        "error_file": frame.filename.rsplit("/", 1)[-1],
        "error_line": frame.lineno,
        "error_function": frame.name,
    }


def _error_slug(err: BaseException, stage: str) -> str:
    text = str(err).lower()
    if isinstance(err, TimeoutError) or "timeout" in text:
        return f"{stage}-timeout"
    if "closed" in text or "target page" in text:
        return f"{stage}-closed"
    if "connection" in text or "network" in text:
        return f"{stage}-connection"
    return f"{stage}-error"


async def _run(bot_id: str) -> int:
    settings = get_settings()
    handle_sigterm()
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
    context = build_context(session_factory, settings, blob, redis, f"job-{bot_id}")
    stage = "launch"
    try:
        launch = _prepare_launch(settings, bot_id)
        async with context.session_factory() as session:
            run = await _service(session, context).get(bot_id)
        display = run.display_name or settings.bot_display_name
        stage = "browser-open"
        await attend(bot_id, run.meeting_url, display, context, launch)
    except join_flow.JoinError as err:
        await fail_run(bot_id, context, err.code)
        return 0
    except Exception as err:
        logger.error(
            "bot_run.failed",
            bot_id=bot_id,
            stage=stage,
            error_slug=_error_slug(err, stage),
            **_error_location(err),
        )
        try:
            await fail_run(bot_id, context, _error_slug(err, stage))
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
