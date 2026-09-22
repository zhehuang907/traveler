"""add shares table for share ownership management

Revision ID: 0006_shares_metadata
Revises: 0005_plan_versions_snapshot_hash
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_shares_metadata"
down_revision: str | None = "0005_plan_versions_snapshot_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shares",
        sa.Column("token", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", name="fk_shares_user_id_users", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp()),
    )
    op.create_index("ix_shares_user_id", "shares", ["user_id"], unique=False)
    op.create_index("ix_shares_plan_id", "shares", ["plan_id"], unique=False)
    op.create_index("ix_shares_created_at", "shares", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_shares_created_at", table_name="shares")
    op.drop_index("ix_shares_plan_id", table_name="shares")
    op.drop_index("ix_shares_user_id", table_name="shares")
    op.drop_table("shares")
