"""启动/触发式清理（单机轻量方案，不引入定时任务）。

- 过期分享快照：``data/share/*.json`` 中 ``expires_at`` 已过期的文件删除；
- 过期会话：``auth_sessions`` 中 ``expires_at`` 已过期的行删除。

触发点：应用启动（lifespan）与创建分享时（create_share）顺手清理一次，
避免过期文件/行无限累积。坏文件/坏行跳过，清理失败不炸启动。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travel_agent.config import Settings
from travel_agent.logging_conf import get_logger

log = get_logger(component="cleanup")


def _utc_naive(dt: datetime) -> datetime:
    """MySQL DATETIME 无时区语义：统一存/比 naive UTC。"""
    return dt.replace(tzinfo=UTC).replace(tzinfo=None) if dt.tzinfo else dt


def cleanup_expired_shares(settings: Settings) -> int:
    """删除已过期的分享快照文件，返回删除数量。"""
    share_dir = Path(settings.data_dir) / "share"
    if not share_dir.is_dir():
        return 0
    now = datetime.now(UTC)
    removed = 0
    for path in share_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            expires = data.get("expires_at")
            if expires and datetime.fromisoformat(expires) < now:
                path.unlink(missing_ok=True)
                removed += 1
        except (json.JSONDecodeError, ValueError, OSError):
            # 坏文件跳过：不因清理任务炸掉启动流程
            continue
    if removed:
        log.info("share_cleanup", removed=removed)
    return removed


async def cleanup_expired_shares_meta(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """删除已过期分享的元数据行（快照文件由 cleanup_expired_shares 负责）。"""
    from typing import Any, cast

    from sqlalchemy import CursorResult, delete

    from travel_agent.db.models import ShareRow
    from travel_agent.db.session import session_scope

    now = _utc_naive(datetime.now(UTC))
    async with session_scope(session_factory) as session:
        result = await session.execute(delete(ShareRow).where(ShareRow.expires_at < now))
    removed = int(cast(CursorResult[Any], result).rowcount or 0)
    if removed:
        log.info("share_meta_cleanup", removed=removed)
    return removed


async def cleanup_expired_sessions(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """删除已过期的会话行，返回删除数量。"""
    from typing import Any, cast

    from sqlalchemy import CursorResult, delete

    from travel_agent.db.models import AuthSessionRow
    from travel_agent.db.session import session_scope

    now = _utc_naive(datetime.now(UTC))
    async with session_scope(session_factory) as session:
        result = await session.execute(
            delete(AuthSessionRow).where(AuthSessionRow.expires_at < now)
        )
    removed = int(cast(CursorResult[Any], result).rowcount or 0)
    if removed:
        log.info("session_cleanup", removed=removed)
    return removed
