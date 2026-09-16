"""行程接口：GET 详情、GET 版本列表、POST 回滚、POST PDF 导出、新增/编辑/删除 CRUD。

所有端点需登录；列表/详情/编辑/删除校验用户所有权。
"""

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from travel_agent.api.deps import get_current_user, get_db, get_settings
from travel_agent.api.errors import AppError, ErrorCode
from travel_agent.config import Settings
from travel_agent.db.models import UserRow
from travel_agent.domain.plan import TripPlan

__all__ = ["router"]

router = APIRouter(prefix="/api/plan", tags=["plan"])


class RollbackRequest(BaseModel):
    version: int


class CreatePlanRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=60)
    start_date: date
    end_date: date
    travelers: int = Field(ge=1, le=50)
    budget_cny: float | None = Field(default=None, ge=0.0)
    title: str | None = None


class PlanListOut(BaseModel):
    """列表项，足够前端渲染"我的行程"。"""

    id: str
    title: str
    destination: str
    start_date: str
    end_date: str
    budget_cny: float | None
    days: int
    updated_at: datetime
    created_at: datetime


class VersionOut(BaseModel):
    version: int
    trigger_snippet: str | None = None
    created_at: datetime | None = None


class PlanOut(BaseModel):
    plan: TripPlan
    plan_version: int
    thread_id: str | None = None


@router.get("", response_model=list[PlanListOut])
async def list_my_plans(
    limit: int = 50,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[PlanListOut]:
    """我的行程列表，按更新时间倒序。"""
    from travel_agent.db import PlanRepository

    repo = PlanRepository(session)
    rows = await repo.list_by_user(user.id, limit=limit)
    out: list[PlanListOut] = []
    for row in rows:
        plan = TripPlan.model_validate_json(row.snapshot_json)
        out.append(
            PlanListOut(
                id=row.id,
                title=row.title,
                destination=plan.destination,
                start_date=plan.start_date.isoformat(),
                end_date=plan.end_date.isoformat(),
                budget_cny=row.budget_cny,
                days=(plan.end_date - plan.start_date).days + 1,
                updated_at=row.updated_at,
                created_at=row.created_at,
            )
        )
    return out


@router.get("/{plan_id}", response_model=PlanOut)
async def get_plan(
    plan_id: str,
    settings: Settings = Depends(get_settings),
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PlanOut:
    from travel_agent.db import PlanRepository

    repo = PlanRepository(session)
    plan = await repo.get_plan(plan_id, user_id=user.id)
    if plan is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, f"行程 {plan_id} 不存在", status_code=404)
    thread_id = await repo.get_thread_id(plan_id, user_id=user.id)
    return PlanOut(plan=plan, plan_version=0, thread_id=thread_id)


@router.get("/{plan_id}/versions", response_model=list[VersionOut])
async def list_versions(
    plan_id: str,
    settings: Settings = Depends(get_settings),
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[VersionOut]:
    from travel_agent.db import PlanRepository

    repo = PlanRepository(session)
    plan = await repo.get_plan(plan_id, user_id=user.id)
    if plan is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, f"行程 {plan_id} 不存在", status_code=404)
    versions = await repo.list_version_numbers(plan_id)
    return [VersionOut(version=v) for v in sorted(versions)]


@router.post("/{plan_id}/rollback", response_model=PlanOut)
async def rollback(
    plan_id: str,
    req: RollbackRequest,
    settings: Settings = Depends(get_settings),
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PlanOut:
    from travel_agent.db import PlanRepository

    repo = PlanRepository(session)
    plan = await repo.get_version(plan_id, req.version, user_id=user.id)
    if plan is None:
        raise AppError(ErrorCode.NOT_FOUND, f"版本 {req.version} 不存在", status_code=404)
    thread_id = await repo.get_thread_id(plan_id, user_id=user.id) or "unknown"
    new_ver = await repo.latest_version_number(plan_id) + 1
    await repo.save_snapshot(plan, thread_id, new_ver, user_id=user.id)
    return PlanOut(plan=plan, plan_version=new_ver, thread_id=thread_id)


@router.post("", response_model=PlanOut)
async def create_plan(
    req: CreatePlanRequest,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PlanOut:
    """手工新建行程，占位：仅创建空骨架，前端跳编辑页补全。"""
    from travel_agent.domain.plan import TripPlan, new_plan_id

    plan_id = new_plan_id()
    plan = TripPlan(
        plan_id=plan_id,
        destination=req.destination,
        start_date=req.start_date,
        end_date=req.end_date,
        travelers=req.travelers,
        budget_cny=req.budget_cny,
        days=[],
    )
    from travel_agent.db import PlanRepository

    repo = PlanRepository(session)
    await repo.save_snapshot(plan, uuid.uuid4().hex, 1, user_id=user.id)
    return PlanOut(plan=plan, plan_version=1, thread_id=None)


@router.delete("/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    """删除行程及其所有版本；非本人返回 404。"""
    from travel_agent.db import PlanRepository

    repo = PlanRepository(session)
    ok = await repo.delete_plan(plan_id, user.id)
    if not ok:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, f"行程 {plan_id} 不存在", status_code=404)


@router.post("/{plan_id}/pdf")
async def export_pdf(
    plan_id: str,
    settings: Settings = Depends(get_settings),
    user: UserRow = Depends(get_current_user),
) -> dict[str, str]:
    """PDF 导出占位：阶段五标记为 TODO，Playwright 渲染打印版 HTML。"""
    raise AppError(
        ErrorCode.SERVICE_UNAVAILABLE,
        "PDF 导出功能暂未启用，请使用浏览器打印",
        status_code=503,
    )
