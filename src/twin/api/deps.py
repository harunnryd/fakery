from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from twin.bots.service import BotService, SqlalchemyBotRepository


def get_session(request: Request) -> AsyncSession:
    return request.app.state.session_factory()


def get_bot_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BotService:
    return BotService(SqlalchemyBotRepository(session))


BotServiceDep = Annotated[BotService, Depends(get_bot_service)]
