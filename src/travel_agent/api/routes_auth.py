"""认证接口：注册 / 登录 / 登出 / 当前用户。

Session Cookie 方案：密码用 bcrypt 哈希存储；登录签发随机会话 token，
库中只存 token 的 SHA-256 哈希；token 通过 HttpOnly Cookie 下发给浏览器。
"""

import re
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from travel_agent.api.deps import get_current_user, get_db, get_settings, hash_token
from travel_agent.api.errors import AppError, ErrorCode
from travel_agent.config import Settings
from travel_agent.db.models import AuthSessionRow, UserRow
from travel_agent.db.repositories import UserRepository

__all__ = ["router"]

router = APIRouter(prefix="/api/auth", tags=["auth"])

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{3,32}$")


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=72)


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str


def _validate_credentials(username: str, password: str) -> None:
    if not _USERNAME_RE.match(username):
        raise AppError(
            ErrorCode.BAD_REQUEST,
            "用户名需为 3-32 位字母、数字、下划线或中文",
            status_code=422,
        )
    if not (any(c.isalpha() for c in password) and any(c.isdigit() for c in password)):
        raise AppError(
            ErrorCode.BAD_REQUEST,
            "密码至少 8 位，且需包含字母和数字",
            status_code=422,
        )


@router.post("/register", response_model=UserOut, status_code=201)
async def register(
    req: RegisterIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserOut:
    from travel_agent.api.security import LoginThrottle

    throttle: LoginThrottle = request.app.state.login_throttle
    client_ip = request.client.host if request.client else "unknown"
    ip_key = f"reg:{client_ip}"
    throttle.check(ip_key)
    try:
        _validate_credentials(req.username, req.password)
    except AppError:
        throttle.record_failure(ip_key)
        raise
    hashed = bcrypt.hashpw(req.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    repo = UserRepository(session)
    user = await repo.create(req.username, hashed)
    if user is None:
        throttle.record_failure(ip_key)
        raise AppError(ErrorCode.BAD_REQUEST, "用户名已被占用", status_code=409)
    throttle.clear(ip_key)
    return UserOut(id=user.id, username=user.username)


@router.post("/login")
async def login(
    req: LoginIn,
    response: Response,
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UserOut:
    from travel_agent.api.security import LoginThrottle

    throttle: LoginThrottle = request.app.state.login_throttle
    client_ip = request.client.host if request.client else "unknown"
    username_key = f"login:{req.username}"
    ip_key = f"login:ip:{client_ip}"
    throttle.check(username_key)
    throttle.check(ip_key)

    repo = UserRepository(session)
    user = await repo.get_by_username(req.username)
    if user is None or not bcrypt.checkpw(
        req.password.encode("utf-8"), user.password_hash.encode("utf-8")
    ):
        throttle.record_failure(username_key)
        throttle.record_failure(ip_key)
        raise AppError(ErrorCode.UNAUTHORIZED, "用户名或密码错误", status_code=401)

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
    session.add(
        AuthSessionRow(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=expires_at,
        )
    )
    # 显式提交：确保会话行立即可被后续请求（复用连接池的新快照）读取，
    # 避免仅在依赖退出时才提交导致的"登录后首个请求"可见性竞态。
    await session.commit()
    # 登录成功：清除该用户名与 IP 的失败计数（含锁定）
    throttle.clear(username_key)
    throttle.clear(ip_key)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return UserOut(id=user.id, username=user.username)


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    request: Request,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: UserRow = Depends(get_current_user),
) -> None:
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await session.execute(
            delete(AuthSessionRow).where(
                AuthSessionRow.token_hash == hash_token(token),
                AuthSessionRow.user_id == user.id,
            )
        )
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=UserOut)
async def me(user: UserRow = Depends(get_current_user)) -> UserOut:
    return UserOut(id=user.id, username=user.username)


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password", status_code=204)
async def change_password(
    req: ChangePasswordIn,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user: UserRow = Depends(get_current_user),
) -> None:
    """修改密码：校验旧密码 → 校验新密码规则 → 更新哈希 → 吊销该用户全部会话。"""
    from sqlalchemy import delete

    from travel_agent.api.security import LoginThrottle
    from travel_agent.db.models import AuthSessionRow

    throttle: LoginThrottle = request.app.state.login_throttle
    client_ip = request.client.host if request.client else "unknown"
    ip_key = f"change_pw:{client_ip}"
    throttle.check(ip_key)

    if not bcrypt.checkpw(req.old_password.encode("utf-8"), user.password_hash.encode("utf-8")):
        throttle.record_failure(ip_key)
        raise AppError(ErrorCode.BAD_REQUEST, "原密码错误", status_code=400)
    try:
        _validate_credentials(user.username, req.new_password)
    except AppError:
        throttle.record_failure(ip_key)
        raise
    if req.old_password == req.new_password:
        raise AppError(ErrorCode.BAD_REQUEST, "新密码不能与原密码相同", status_code=400)

    hashed = bcrypt.hashpw(req.new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    repo = UserRepository(session)
    await repo.update_password(user.id, hashed)
    # 改密后吊销该用户全部会话：旧 token 立即失效，需重新登录
    await session.execute(delete(AuthSessionRow).where(AuthSessionRow.user_id == user.id))
    throttle.clear(ip_key)
