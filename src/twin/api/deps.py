from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from twin.bots.service import (
    BotService,
    SqlalchemyBotRepository,
    SqlalchemySubscriptionRepository,
    SubscriptionService,
)


def get_session(request: Request) -> AsyncSession:
    return request.app.state.session_factory()


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


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
