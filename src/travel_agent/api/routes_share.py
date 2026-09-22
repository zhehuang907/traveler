"""只读分享接口：POST /api/share 生成快照 + GET /api/share/{token} 只读访问。

ADR-009：无鉴权只读快照 + 不可猜测 token + 可选过期。
"""

import json
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from travel_agent.api.deps import get_current_user, get_db, get_settings
from travel_agent.api.errors import AppError, ErrorCode
from travel_agent.config import Settings
from travel_agent.db.models import UserRow
from travel_agent.domain.plan import TripPlan

__all__ = ["router"]

router = APIRouter(prefix="/api/share", tags=["share"])


class ShareRequest(BaseModel):
    plan_id: str
    version: int | None = None
    expires_days: int | None = None


class ShareResponse(BaseModel):
    token: str
    url: str
    expires_at: datetime | None = None


class ShareSnapshot(BaseModel):
    plan: TripPlan
    created_at: datetime
    expires_at: datetime | None = None


@router.post("", response_model=ShareResponse)
async def create_share(
    req: ShareRequest,
    settings: Settings = Depends(get_settings),
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ShareResponse:
    from pathlib import Path

    from travel_agent.db import PlanRepository, ShareRepository

    repo = PlanRepository(session)
    if req.version is not None:
        plan = await repo.get_version(req.plan_id, req.version, user_id=user.id)
    else:
        plan = await repo.get_plan(req.plan_id, user_id=user.id)
    if plan is None:
        raise AppError(
            ErrorCode.PLAN_NOT_FOUND,
            f"行程 {req.plan_id} 不存在",
            status_code=404,
        )
    token = secrets.token_urlsafe(32)

    share_dir = Path(settings.data_dir) / "share"
    share_dir.mkdir(parents=True, exist_ok=True)
    # 顺手清理过期分享快照（低频路径，避免文件无限累积）
    from travel_agent.services.cleanup import cleanup_expired_shares

    cleanup_expired_shares(settings)
    expires_at = None
    if req.expires_days is not None:
        from datetime import timedelta

        expires_at = datetime.now(UTC) + timedelta(days=req.expires_days)
    snapshot = ShareSnapshot(
        plan=plan,
        created_at=datetime.now(UTC),
        expires_at=expires_at,
    )
    (share_dir / f"{token}.json").write_text(
        # 排除计算字段：回读走 model_validate（extra=forbid），计算字段不可作为输入
        snapshot.model_dump_json(exclude_computed_fields=True),
        encoding="utf-8",
    )
    # 元数据入库：归属当前用户，支撑「我的分享」列表与撤销
    await ShareRepository(session).create(
        token=token,
        user_id=user.id,
        plan_id=req.plan_id,
        version=req.version,
        expires_at=expires_at,
    )
    return ShareResponse(
        token=token,
        url=f"/api/share/{token}",
        expires_at=snapshot.expires_at,
    )


class ShareSummary(BaseModel):
    """我的分享列表项（元数据 + 快照摘要，不含完整行程）。"""

    token: str
    plan_id: str
    version: int | None = None
    destination: str
    start_date: str | None = None
    end_date: str | None = None
    expires_at: datetime | None = None
    created_at: datetime


@router.get("/mine", response_model=list[ShareSummary])
async def list_my_shares(
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[ShareSummary]:
    """我的分享列表（新→旧；已过期文件顺带清理标记）。"""
    from pathlib import Path

    from travel_agent.db import ShareRepository

    rows = await ShareRepository(session).list_by_user(user.id)
    out: list[ShareSummary] = []
    for row in rows:
        share_file = Path(settings.data_dir) / "share" / f"{row.token}.json"
        if not share_file.exists():
            continue  # 文件已被清理（过期/手工删除），元数据行不展示
        try:
            data = json.loads(share_file.read_text(encoding="utf-8"))
            plan = data.get("plan", {})
            destination = plan.get("destination", "")
            start_date = plan.get("start_date")
            end_date = plan.get("end_date")
        except (json.JSONDecodeError, OSError):
            destination, start_date, end_date = "", None, None
        out.append(
            ShareSummary(
                token=row.token,
                plan_id=row.plan_id,
                version=row.version,
                destination=destination,
                start_date=start_date,
                end_date=end_date,
                expires_at=row.expires_at,
                created_at=row.created_at,
            )
        )
    return out


@router.delete("/{token}", status_code=204)
async def revoke_share(
    token: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    """撤销分享：删除元数据行 + 快照文件（仅限创建者本人）。"""
    from pathlib import Path

    from travel_agent.db import ShareRepository

    share_repo = ShareRepository(session)
    row = await share_repo.get_for_user(token, user.id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "分享不存在", status_code=404)
    await share_repo.delete(token, user.id)
    share_file = Path(settings.data_dir) / "share" / f"{token}.json"
    share_file.unlink(missing_ok=True)


@router.get("/{token}", response_model=ShareSnapshot)
async def get_share(
    token: str,
    settings: Settings = Depends(get_settings),
) -> ShareSnapshot:
    from pathlib import Path

    share_file = Path(settings.data_dir) / "share" / f"{token}.json"
    if not share_file.exists():
        raise AppError(ErrorCode.NOT_FOUND, "分享链接无效或已过期", status_code=404)
    data = json.loads(share_file.read_text(encoding="utf-8"))
    snapshot = ShareSnapshot.model_validate(data)
    if snapshot.expires_at is not None and datetime.now(UTC) > snapshot.expires_at:
        share_file.unlink(missing_ok=True)
        raise AppError(ErrorCode.NOT_FOUND, "分享链接已过期", status_code=404)
    return snapshot
