from fastapi import APIRouter, status

from twin.api.deps import BotServiceDep, RedisDep
from twin.api.schemas import (
    BotResource,
    CreateBotRequest,
    TranscriptResponse,
    TranscriptSegmentResource,
)
from twin.bots.models import TranscriptSegment
from twin.bots.queue import enqueue_run, request_cancel

router = APIRouter(prefix="/v1", tags=["bots"])


@router.post("/bots", status_code=status.HTTP_202_ACCEPTED)
async def create_bot(
    body: CreateBotRequest, service: BotServiceDep, redis: RedisDep
) -> BotResource:
    run = await service.create(meeting_url=str(body.meeting_url), display_name=body.display_name)
    await enqueue_run(redis, run.id)
    return BotResource.model_validate(run)


@router.get("/bots/{bot_id}")
async def get_bot(bot_id: str, service: BotServiceDep) -> BotResource:
    run = await service.get(bot_id)
    return BotResource.model_validate(run)


@router.delete("/bots/{bot_id}", status_code=status.HTTP_202_ACCEPTED)
async def cancel_bot(bot_id: str, service: BotServiceDep, redis: RedisDep) -> dict:
    await service.get(bot_id)
    await request_cancel(redis, bot_id)
    return {"id": bot_id, "status": "cancelling"}


@router.get("/bots/{bot_id}/transcript")
async def get_bot_transcript(bot_id: str, service: BotServiceDep) -> TranscriptResponse:
    segments: list[TranscriptSegment] = await service.transcript(bot_id)
    return TranscriptResponse(
        bot_id=bot_id,
        segments=[TranscriptSegmentResource.model_validate(seg) for seg in segments],
    )
