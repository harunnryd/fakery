"""allow a bot to reserve capacity again after release

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-09

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE capacity_reservations DROP CONSTRAINT IF EXISTS "
        "capacity_reservations_bot_id_key"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_capacity_active_bot "
        "ON capacity_reservations (bot_id) WHERE released_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_capacity_active_bot")
    op.create_unique_constraint(
        "capacity_reservations_bot_id_key", "capacity_reservations", ["bot_id"]
    )
