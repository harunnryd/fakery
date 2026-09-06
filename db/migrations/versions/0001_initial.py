"""initial tables: bots, transcript_segments, meeting_notes

Revision ID: 0001
Revises:
Create Date: 2026-09-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("meeting_url", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bots_status"), "bots", ["status"])

    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("bot_run_id", sa.String(length=64), nullable=False),
        sa.Column("speaker", sa.String(length=128), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["bot_run_id"], ["bots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transcript_segments_bot_run",
        "transcript_segments",
        ["bot_run_id", "start_ms"],
    )

    op.create_table(
        "meeting_notes",
        sa.Column("bot_run_id", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("key_points", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("action_items", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["bot_run_id"], ["bots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("bot_run_id"),
    )


def downgrade() -> None:
    op.drop_table("meeting_notes")
    op.drop_index("ix_transcript_segments_bot_run", table_name="transcript_segments")
    op.drop_table("transcript_segments")
    op.drop_index(op.f("ix_bots_status"), table_name="bots")
    op.drop_table("bots")
