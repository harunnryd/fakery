from fastapi import APIRouter, Query, status

from twin.api.deps import BotIdDep, BotServiceDep, RedisDep
from twin.api.schemas import (
    BotListResponse,
    BotResource,
    CreateBotRequest,
    NotesResource,
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


@router.get("/bots")
async def list_bots(
    service: BotServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> BotListResponse:
    runs, next_cursor = await service.list_runs(limit, cursor)
    return BotListResponse(
        bots=[BotResource.model_validate(run) for run in runs],
        next_cursor=next_cursor,
    )


@router.get("/bots/{bot_id}")
async def get_bot(bot_id: BotIdDep, service: BotServiceDep) -> BotResource:
    run = await service.get(bot_id)
    return BotResource.model_validate(run)


@router.delete("/bots/{bot_id}", status_code=status.HTTP_202_ACCEPTED)
async def cancel_bot(bot_id: BotIdDep, service: BotServiceDep, redis: RedisDep) -> dict:
    await service.get(bot_id)
    await request_cancel(redis, bot_id)
    return {"id": bot_id, "status": "cancelling"}


@router.get("/bots/{bot_id}/transcript")
async def get_bot_transcript(bot_id: BotIdDep, service: BotServiceDep) -> TranscriptResponse:
    segments: list[TranscriptSegment] = await service.transcript(bot_id)
    return TranscriptResponse(
        bot_id=bot_id,
        segments=[TranscriptSegmentResource.model_validate(seg) for seg in segments],
    )


@router.get("/bots/{bot_id}/notes")
async def get_bot_notes(bot_id: BotIdDep, service: BotServiceDep) -> NotesResource:
    note = await service.get_notes(bot_id)
    return NotesResource.model_validate(note)
