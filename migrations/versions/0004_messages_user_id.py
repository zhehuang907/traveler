"""add messages.user_id for per-user conversation history

Revision ID: 0004_messages_user_id
Revises: 0003_plans_user_id
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_messages_user_id"
down_revision: str | None = "0003_plans_user_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 注意：MySQL 不支持 batch_alter_table，须用原生 add_column/create_foreign_key
    op.add_column(
        "messages",
        sa.Column("user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_messages_user_id_users",
        "messages",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_messages_user_id", "messages", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_messages_user_id", table_name="messages")
    op.drop_constraint("fk_messages_user_id_users", "messages", type_="foreignkey")
    op.drop_column("messages", "user_id")
