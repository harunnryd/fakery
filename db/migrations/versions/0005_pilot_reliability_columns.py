"""add pilot idempotency, recording, and delivery state

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bots", sa.Column("meeting_key", sa.String(length=256), nullable=True))
    op.add_column("bots", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.add_column("bots", sa.Column("stop_reason", sa.String(length=32), nullable=True))
    op.add_column(
        "bots",
        sa.Column(
            "recording_status", sa.String(length=32), nullable=False, server_default="pending"
        ),
    )
    op.add_column("bots", sa.Column("recording_bytes", sa.BigInteger(), nullable=True))
    op.add_column("bots", sa.Column("recording_duration_ms", sa.Integer(), nullable=True))
    op.add_column(
        "bots", sa.Column("recording_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "bots",
        sa.Column("recording_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "bots", sa.Column("checkpoint_manifest_uri", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "bots",
        sa.Column(
            "finalization_status", sa.String(length=32), nullable=False, server_default="pending"
        ),
    )
    op.execute("UPDATE bots SET recording_status = 'unknown', finalization_status = 'unknown'")
    op.execute(
        "UPDATE bots SET meeting_key = 'meet.google.com/' || lower("
        "regexp_replace(regexp_replace(meeting_url, '^https://meet.google.com/', ''), "
        "'[/?#].*$', '')"
        ") WHERE meeting_key IS NULL AND meeting_url LIKE 'https://meet.google.com/%'"
    )
    op.create_index("ix_bots_meeting_key", "bots", ["meeting_key"])
    op.execute(
        "WITH ranked AS ("
        "SELECT id, ROW_NUMBER() OVER (PARTITION BY meeting_key ORDER BY created_at, id) AS rank "
        "FROM bots WHERE meeting_key IS NOT NULL AND status IN "
        "('queued', 'joining', 'joined', 'recording', 'processing')) "
        "UPDATE bots AS b SET status = 'failed', error_code = 'duplicate-meeting', "
        "stop_reason = 'failure' FROM ranked "
        "WHERE b.id = ranked.id AND ranked.rank > 1"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_bots_active_meeting "
        "ON bots (meeting_key) WHERE status IN "
        "('queued', 'joining', 'joined', 'recording', 'processing')"
    )
    op.create_unique_constraint("uq_bots_idempotency_key", "bots", ["idempotency_key"])

    op.add_column(
        "webhook_deliveries", sa.Column("subscription_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("event_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
    )
    op.add_column("webhook_deliveries", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column("webhook_deliveries", sa.Column("last_status_code", sa.Integer(), nullable=True))
    op.add_column(
        "webhook_deliveries", sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "webhook_deliveries", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "webhook_deliveries", sa.Column("lease_token", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE webhook_deliveries SET attempts = COALESCE(attempts, 0), "
        "payload = COALESCE(payload, '{}'::jsonb)"
    )
    op.execute(
        "UPDATE webhook_deliveries AS d SET subscription_id = s.id "
        "FROM webhook_subscriptions AS s "
        "WHERE d.subscription_id IS NULL AND d.url = s.url"
    )
    op.execute(
        "UPDATE webhook_deliveries SET event_id = COALESCE(payload->>'event_id', id) "
        "WHERE event_id IS NULL"
    )
    op.execute(
        "WITH ranked AS ("
        "SELECT id, ROW_NUMBER() OVER ("
        "PARTITION BY subscription_id, (payload->>'bot_id') "
        "ORDER BY created_at, id"
        ") - 1 AS next_sequence FROM webhook_deliveries) "
        "UPDATE webhook_deliveries AS d SET sequence = ranked.next_sequence "
        "FROM ranked WHERE d.id = ranked.id"
    )
    op.alter_column("webhook_deliveries", "event_id", nullable=False)
    op.create_index(
        "ix_webhook_deliveries_subscription_id", "webhook_deliveries", ["subscription_id"]
    )
    op.create_index("ix_webhook_deliveries_event_id", "webhook_deliveries", ["event_id"])
    op.create_index(
        "ix_webhook_delivery_order", "webhook_deliveries", ["subscription_id", "sequence"]
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_webhook_delivery_stream_sequence "
        "ON webhook_deliveries (subscription_id, (payload->>'bot_id'), sequence)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_webhook_delivery_stream_sequence")
    op.drop_index("ix_webhook_delivery_order", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_event_id", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_subscription_id", table_name="webhook_deliveries")
    for column in (
        "lease_expires_at",
        "lease_token",
        "sent_at",
        "dead_at",
        "last_status_code",
        "last_error",
        "status",
        "sequence",
        "event_id",
        "subscription_id",
    ):
        op.drop_column("webhook_deliveries", column)
    op.execute("DROP INDEX uq_bots_active_meeting")
    op.drop_constraint("uq_bots_idempotency_key", "bots", type_="unique")
    op.drop_index("ix_bots_meeting_key", table_name="bots")
    for column in (
        "finalization_status",
        "checkpoint_manifest_uri",
        "recording_partial",
        "recording_expires_at",
        "recording_duration_ms",
        "recording_bytes",
        "recording_status",
        "stop_reason",
        "idempotency_key",
        "meeting_key",
    ):
        op.drop_column("bots", column)
