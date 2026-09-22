"""持久化 ORM 模型（MySQL 主库；CLI 会话记忆用独立 SQLite）。

- users / auth_sessions：登录注册与会话 Cookie 鉴权
- plans / plan_versions：行程快照与逐版本历史（阶段四启用回滚）
- conversation_contexts：会话内跨轮记忆（分批补充时间/地点/人数；chitchat 不入库）
- user_preferences：长期偏好（阶段四，CLI 用）

注意：MySQL 标识符长度上限 64，新增约束/索引须给出显式短 ``name``。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from travel_agent.db.base import Base


class UserRow(Base):
    """注册用户。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class AuthSessionRow(Base):
    """登录会话（存 token 的 SHA-256 哈希，杜绝明文；HttpOnly Cookie）。"""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", name="fk_auth_sessions_user_id_users", ondelete="CASCADE"),
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    last_seen_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class ConversationContextRow(Base):
    """会话内跨轮记忆：每轮结束覆盖更新该 (user, thread) 的累计 brief。

    只有参与行程构建的上下文被持久化；chitchat / 开放问答不写入任何留存表。
    """

    __tablename__ = "conversation_contexts"
    __table_args__ = (UniqueConstraint("user_id", "thread_id", name="uq_ctx_user_thread"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", name="fk_ctx_user_id_users", ondelete="CASCADE"),
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    brief_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class PlanRow(Base):
    """一个行程单（每次保存写一条版本行）。"""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", name="fk_plans_user_id_users", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    destination: Mapped[str] = mapped_column(String(60), default="")
    budget_cny: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class PlanVersionRow(Base):
    """行程版本（snapshot + diff），支撑历史查看与回滚。"""

    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("plan_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("plans.id", name="fk_plan_versions_plan_id_plans", ondelete="CASCADE"),
        index=True,
    )
    version: Mapped[int] = mapped_column()
    snapshot_json: Mapped[str] = mapped_column(Text)
    # 快照 sha256（hex），版本去重走哈希比对（有索引），不再全文本扫描
    snapshot_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    diff_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class MessageRow(Base):
    """会话消息留痕与 token 用量。

    user_id 可空以兼容 CLI 旧行；web 端写入时必填，按用户隔离历史。
    """

    __tablename__ = "messages"

    # 自增序列保证同一会话消息的稳定先后（created_at 仅秒级精度，id 为随机 UUID）
    seq: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", name="fk_messages_user_id_users", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    token_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(), index=True
    )


class UserPreferenceRow(Base):
    """长期偏好（scope_key 通常为 thread_id 或 'global'）。"""

    __tablename__ = "user_preferences"
    __table_args__ = (UniqueConstraint("scope_key", "pref_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scope_key: Mapped[str] = mapped_column(String(64), index=True)
    pref_key: Mapped[str] = mapped_column(String(64))
    value_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())


class ShareRow(Base):
    """分享元数据（快照本体仍存 data/share/*.json）。

    user_id 非空：分享归创建者所有，支撑「我的分享」列表与撤销；
    存量文件分享（无行）不受影响，撤销仅作用于新分享。
    """

    __tablename__ = "shares"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", name="fk_shares_user_id_users", ondelete="CASCADE"),
        index=True,
    )
    plan_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.current_timestamp(), index=True
    )
