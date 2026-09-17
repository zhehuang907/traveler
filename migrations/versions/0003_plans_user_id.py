"""add plans.user_id for user ownership

Revision ID: 0003_plans_user_id
Revises: 0002_auth_and_contexts
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_plans_user_id"
down_revision: str | None = "0002_auth_and_contexts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 注意：MySQL 不支持 batch_alter_table，须用原生 add_column/create_foreign_key
    op.add_column(
        "plans",
        sa.Column("user_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_plans_user_id_users",
        "plans",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_plans_user_id", "plans", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_plans_user_id", table_name="plans")
    op.drop_constraint("fk_plans_user_id_users", "plans", type_="foreignkey")
    op.drop_column("plans", "user_id")
