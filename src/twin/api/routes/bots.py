import hashlib

from fastapi import APIRouter, Header, Query, Response, status

from twin.api.deps import BlobDep, BotIdDep, BotServiceDep, RedisDep
from twin.api.schemas import (
    BotListResponse,
    BotResource,
    CreateBotRequest,
    NotesResource,
    TranscriptResponse,
    TranscriptSegmentResource,
)
from twin.bots.models import TranscriptSegment
from twin.bots.queue import request_cancel
from twin.core.errors import make_error
from twin.core.time import utcnow

router = APIRouter(prefix="/v1", tags=["bots"])


@router.post("/bots", status_code=status.HTTP_202_ACCEPTED)
async def create_bot(
    body: CreateBotRequest,
    service: BotServiceDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> BotResource:
    run, _ = await service.create_or_get(
        meeting_url=str(body.meeting_url),
        display_name=body.display_name,
        idempotency_key=idempotency_key,
    )
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
    run = await service.get(bot_id)
    if run.status == "queued":
        await service.cancel(bot_id)
        return {"id": bot_id, "status": "failed", "stop_reason": "cancelled"}
    if run.status in {"completed", "failed"}:
        return {"id": bot_id, "status": run.status, "stop_reason": run.stop_reason}
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


@router.get("/bots/{bot_id}/recording")
async def get_bot_recording(bot_id: BotIdDep, service: BotServiceDep, blob: BlobDep) -> Response:
    run = await service.get(bot_id)
    if not run.recording_uri:
        raise make_error("not-found", detail=f"recording for {bot_id} does not exist")
    if run.recording_expires_at is not None and run.recording_expires_at <= utcnow():
        raise make_error("not-found", detail=f"recording for {bot_id} has expired")
    key = run.recording_uri.split("/", 3)[-1]
    try:
        data = await blob.get(key)
    except Exception as err:
        raise make_error("upstream-unavailable", detail="recording storage is unavailable") from err
    if run.recording_sha256 and hashlib.sha256(data).hexdigest() != run.recording_sha256:
        raise make_error("upstream-unavailable", detail="recording checksum verification failed")
    headers = {"X-Recording-SHA256": run.recording_sha256 or ""}
    return Response(content=data, media_type="audio/webm", headers=headers)
