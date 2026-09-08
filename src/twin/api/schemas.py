from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl

from twin.bots.state import BotStatus


class CreateBotRequest(BaseModel):
    meeting_url: HttpUrl
    display_name: str | None = None


class BotResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    meeting_url: str
    display_name: str | None
    status: BotStatus
    error_code: str | None
    created_at: datetime
    joined_at: datetime | None
    left_at: datetime | None


class TranscriptSegmentResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    speaker: str | None
    text: str
    start_ms: int
    end_ms: int


class TranscriptResponse(BaseModel):
    bot_id: str
    segments: list[TranscriptSegmentResource]


class ActionItemResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    owner: str | None = None
    due: str | None = None


class NotesResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bot_run_id: str
    summary: str
    key_points: list[str]
    action_items: list[ActionItemResource]


class BotListResponse(BaseModel):
    bots: list[BotResource]
    next_cursor: str | None = None


class CreateWebhookRequest(BaseModel):
    url: HttpUrl


class WebhookResource(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    url: str
    created_at: datetime
