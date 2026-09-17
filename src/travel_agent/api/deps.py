"""FastAPI 依赖集合。"""

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.api.errors import AppError, ErrorCode
from travel_agent.config import Settings
from travel_agent.config import get_settings as load_settings
from travel_agent.db.models import AuthSessionRow, UserRow
from travel_agent.services.llm import StructuredLLM, build_structured_llm

__all__ = [
    "get_current_user",
    "get_db",
    "get_llm",
    "get_request_id",
    "get_settings",
    "get_tool_context",
    "hash_token",
]


def get_settings() -> Settings:
    """注入配置（进程级单例）。"""
    return load_settings()


def get_request_id(request: Request) -> str:
    """注入当前请求 ID（由 RequestID 中间件写入 state）。"""
    request_id: str | None = getattr(request.state, "request_id", None)
    return request_id or "-"


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """复用应用级连接池的会话依赖：正常提交，异常回滚。"""
    from travel_agent.db import session_scope

    factory = request.app.state.session_factory
    async with session_scope(factory) as session:
        yield session


def get_llm(settings: Settings = Depends(get_settings)) -> StructuredLLM | None:
    """注入结构化 LLM；未配置 Key 返回 None（由路由给出明确错误）。"""
    return build_structured_llm(settings)


def get_tool_context(request: Request) -> ToolRegistry:
    """注入工具注册表（缓存/重试/降级链统一装配，测试可整体替换）。"""
    settings: Settings = request.app.state.settings
    return ToolRegistry(settings)


def hash_token(token: str) -> str:
    """对会话 token 做 SHA-256，库中只存哈希。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserRow:
    """从 HttpOnly Cookie 解析当前登录用户；无效/过期则 401。"""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise AppError(ErrorCode.UNAUTHORIZED, "请先登录", status_code=401)
    token_hash = hash_token(token)
    row = (
        await session.execute(select(AuthSessionRow).where(AuthSessionRow.token_hash == token_hash))
    ).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.UNAUTHORIZED, "登录状态无效，请重新登录", status_code=401)
    # MySQL DATETIME 列无时区语义，回读为 naive；统一按 UTC 比较
    if row.expires_at is not None:
        exp = row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp < datetime.now(UTC):
            raise AppError(ErrorCode.UNAUTHORIZED, "登录已过期，请重新登录", status_code=401)
    user = await session.get(UserRow, row.user_id)
    if user is None:
        raise AppError(ErrorCode.UNAUTHORIZED, "用户不存在", status_code=401)
    return user
