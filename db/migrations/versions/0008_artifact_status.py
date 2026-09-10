"""add transcript and notes artifact status

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column(
            "transcription_status", sa.String(length=32), nullable=False, server_default="pending"
        ),
    )
    op.add_column("bots", sa.Column("transcription_error", sa.String(length=64), nullable=True))
    op.add_column(
        "bots",
        sa.Column("notes_status", sa.String(length=32), nullable=False, server_default="pending"),
    )
    op.add_column("bots", sa.Column("notes_error", sa.String(length=64), nullable=True))
    op.execute("UPDATE bots SET transcription_status = 'unknown', notes_status = 'unknown'")


def downgrade() -> None:
    op.drop_column("bots", "notes_error")
    op.drop_column("bots", "notes_status")
    op.drop_column("bots", "transcription_error")
    op.drop_column("bots", "transcription_status")
