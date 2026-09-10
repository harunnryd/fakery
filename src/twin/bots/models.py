from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from twin.bots.state import BotStatus
from twin.core.time import utcnow


class Base(DeclarativeBase):
    pass


class CapacityCounter(Base):
    __tablename__ = "capacity_counters"

    tenant: Mapped[str] = mapped_column(String(128), primary_key=True)
    active_count: Mapped[int] = mapped_column(default=0, nullable=False)
    limit: Mapped[int] = mapped_column(default=3, nullable=False)


class CapacityReservation(Base):
    __tablename__ = "capacity_reservations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(128), index=True)
    bot_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("bots.id", ondelete="CASCADE"), nullable=False
    )
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BotRun(Base):
    __tablename__ = "bots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    meeting_url: Mapped[str] = mapped_column(String(512), nullable=False)
    meeting_key: Mapped[str | None] = mapped_column(String(256), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default=BotStatus.QUEUED.value, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recording_uri: Mapped[str | None] = mapped_column(String(512))
    recording_sha256: Mapped[str | None] = mapped_column(String(64))
    recording_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    recording_bytes: Mapped[int | None]
    recording_duration_ms: Mapped[int | None]
    recording_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recording_partial: Mapped[bool] = mapped_column(default=False, nullable=False)
    checkpoint_manifest_uri: Mapped[str | None] = mapped_column(String(512))
    finalization_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    transcription_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    transcription_error: Mapped[str | None] = mapped_column(String(64))
    notes_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    notes_error: Mapped[str | None] = mapped_column(String(64))
    dispatch_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    dispatch_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    dispatch_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_last_error: Mapped[str | None] = mapped_column(Text)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bot_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("bots.id", ondelete="CASCADE"), nullable=False
    )
    speaker: Mapped[str | None] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    start_ms: Mapped[int]
    end_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_transcript_segments_bot_run", TranscriptSegment.bot_run_id, TranscriptSegment.start_ms)


class MeetingNote(Base):
    __tablename__ = "meeting_notes"

    bot_run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("bots.id", ondelete="CASCADE"), primary_key=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    key_points: Mapped[list] = mapped_column(JSONB, default=list)
    action_items: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subscription_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    sequence: Mapped[int] = mapped_column(default=0, nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    next_try_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_status_code: Mapped[int | None]
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_webhook_delivery_order",
    WebhookDelivery.subscription_id,
    WebhookDelivery.sequence,
)
