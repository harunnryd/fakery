"""add durable bot dispatch intent fields

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column(
            "dispatch_status", sa.String(length=16), nullable=False, server_default="pending"
        ),
    )
    op.add_column(
        "bots", sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "bots", sa.Column("dispatch_enqueued_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("bots", sa.Column("dispatch_last_error", sa.Text(), nullable=True))
    op.create_index("ix_bots_dispatch_status", "bots", ["dispatch_status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_bots_dispatch_status", table_name="bots")
    op.drop_column("bots", "dispatch_last_error")
    op.drop_column("bots", "dispatch_enqueued_at")
    op.drop_column("bots", "dispatch_attempts")
    op.drop_column("bots", "dispatch_status")
