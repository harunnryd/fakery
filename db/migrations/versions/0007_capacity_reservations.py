"""add atomic pilot capacity reservations

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capacity_counters",
        sa.Column("tenant", sa.String(length=128), nullable=False),
        sa.Column("active_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("limit", sa.Integer(), nullable=False, server_default="3"),
        sa.PrimaryKeyConstraint("tenant"),
    )
    op.execute(
        'INSERT INTO capacity_counters (tenant, active_count, "limit") '
        "VALUES ('default', 0, 3) ON CONFLICT (tenant) DO NOTHING"
    )
    op.create_table(
        "capacity_reservations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant", sa.String(length=128), nullable=False),
        sa.Column("bot_id", sa.String(length=64), nullable=False),
        sa.Column("owner", sa.String(length=128), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["bot_id"], ["bots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_capacity_reservations_tenant", "capacity_reservations", ["tenant"])
    op.execute(
        "CREATE UNIQUE INDEX uq_capacity_active_bot ON capacity_reservations (bot_id) "
        "WHERE released_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_capacity_active_bot")
    op.drop_index("ix_capacity_reservations_tenant", table_name="capacity_reservations")
    op.drop_table("capacity_reservations")
    op.drop_table("capacity_counters")
