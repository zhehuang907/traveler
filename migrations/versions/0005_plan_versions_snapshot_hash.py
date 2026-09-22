"""add plan_versions.snapshot_hash for hash-based dedup

Revision ID: 0005_plan_versions_snapshot_hash
Revises: 0004_messages_user_id
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_plan_versions_snapshot_hash"
down_revision: str | None = "0004_messages_user_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plan_versions",
        sa.Column("snapshot_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_plan_versions_snapshot_hash",
        "plan_versions",
        ["snapshot_hash"],
        unique=False,
    )
    # 存量版本行回填哈希（sha256 hex，与 Python hashlib 输出一致），否则旧行无法参与去重
    op.execute(
        "UPDATE plan_versions SET snapshot_hash = SHA2(snapshot_json, 256) "
        "WHERE snapshot_hash IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_plan_versions_snapshot_hash", table_name="plan_versions")
    op.drop_column("plan_versions", "snapshot_hash")
