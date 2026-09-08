import hmac
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


def get_session(request: Request) -> AsyncSession:
    return request.app.state.session_factory()


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_app_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if settings is not None else get_settings()


async def require_api_key(
    request: Request, settings: Annotated[Settings, Depends(get_app_settings)]
) -> None:
    if not settings.api_key:
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
    return BotService(SqlalchemyBotRepository(session))


def get_subscription_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SubscriptionService:
    return SubscriptionService(SqlalchemySubscriptionRepository(session))


BotServiceDep = Annotated[BotService, Depends(get_bot_service)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SubscriptionServiceDep = Annotated[SubscriptionService, Depends(get_subscription_service)]
BotIdDep = Annotated[str, Depends(bind_bot_id)]
