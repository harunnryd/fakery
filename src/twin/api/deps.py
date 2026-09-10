import hmac
from collections.abc import AsyncIterator
from typing import Annotated

import structlog
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from twin.bots.service import (
    BotService,
    SqlalchemyBotRepository,
    SqlalchemySubscriptionRepository,
    SubscriptionService,
)
from twin.core.config import Settings, get_settings
from twin.core.errors import make_error
from twin.storage.blob import BlobStore
from twin.webhooks.dispatch import DbOutbox


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_blob(request: Request) -> BlobStore:
    return request.app.state.blob


def get_app_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if settings is not None else get_settings()


async def require_api_key(
    request: Request, settings: Annotated[Settings, Depends(get_app_settings)]
) -> None:
    if not settings.api_key and settings.env == "dev":
        return
    presented = request.headers.get("x-api-key", "")
    if not presented or not hmac.compare_digest(presented, settings.api_key):
        raise make_error("unauthorized", detail="invalid api key")


def bind_bot_id(bot_id: str) -> str:
    structlog.contextvars.bind_contextvars(bot_id=bot_id)
    return bot_id


def get_bot_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BotService:
    return BotService(SqlalchemyBotRepository(session), events=DbOutbox(session))


def get_subscription_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubscriptionService:
    return SubscriptionService(SqlalchemySubscriptionRepository(session))


BotServiceDep = Annotated[BotService, Depends(get_bot_service)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
BlobDep = Annotated[BlobStore, Depends(get_blob)]
SubscriptionServiceDep = Annotated[SubscriptionService, Depends(get_subscription_service)]
BotIdDep = Annotated[str, Depends(bind_bot_id)]
