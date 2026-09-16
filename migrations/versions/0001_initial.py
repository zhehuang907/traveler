"""initial schema: plans, plan_versions, messages, user_preferences

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("destination", sa.String(length=60), nullable=False),
        sa.Column("budget_cny", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
    )
    op.create_index(op.f("ix_plans_thread_id"), "plans", ["thread_id"], unique=False)

    op.create_table(
        "plan_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("diff_json", sa.Text(), nullable=True),
        sa.Column("trigger_message_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            name=op.f("fk_plan_versions_plan_id_plans"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_versions")),
        sa.UniqueConstraint("plan_id", "version", name=op.f("uq_plan_versions_plan_id")),
    )
    op.create_index(op.f("ix_plan_versions_plan_id"), "plan_versions", ["plan_id"], unique=False)

    op.create_table(
        "messages",
        sa.Column("seq", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_messages")),
        sa.UniqueConstraint("id", name=op.f("uq_messages_id")),
    )
    op.create_index(op.f("ix_messages_thread_id"), "messages", ["thread_id"], unique=False)
    op.create_index(op.f("ix_messages_created_at"), "messages", ["created_at"], unique=False)

    op.create_table(
        "user_preferences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("pref_key", sa.String(length=64), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_preferences")),
        sa.UniqueConstraint("scope_key", "pref_key", name=op.f("uq_user_preferences_scope_key")),
    )
    op.create_index(
        op.f("ix_user_preferences_scope_key"),
        "user_preferences",
        ["scope_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_preferences_scope_key"), table_name="user_preferences")
    op.drop_table("user_preferences")
    op.drop_index(op.f("ix_messages_created_at"), table_name="messages")
    op.drop_index(op.f("ix_messages_thread_id"), table_name="messages")
    op.drop_table("messages")
    op.drop_index(op.f("ix_plan_versions_plan_id"), table_name="plan_versions")
    op.drop_table("plan_versions")
    op.drop_index(op.f("ix_plans_thread_id"), table_name="plans")
    op.drop_table("plans")
