"""行程接口：GET 详情、GET 版本列表、POST 回滚、PDF 导出、新增/删除 CRUD。

阶段八新增：PUT 手动编辑保存（存档定向修改）与 POST AI 优化（整体重排）；
两者都写递增新版本（plan_versions），回滚机制不受影响。
所有端点需登录；列表/详情/编辑/删除校验用户所有权。
"""

import json as _json
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from travel_agent.agent.optimize import optimize_saved_plan
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.api.deps import (
    get_current_user,
    get_db,
    get_llm,
    get_settings,
    get_tool_context,
)
from travel_agent.api.errors import AppError, ErrorCode
from travel_agent.config import Settings
from travel_agent.db import PlanRepository
from travel_agent.db.models import PlanVersionRow, UserRow
from travel_agent.domain.diff import PlanDiff, compute_diff
from travel_agent.domain.plan import TripPlan, new_plan_id
from travel_agent.domain.plan_edit import apply_manual_edit
from travel_agent.services.llm import LLMError, StructuredLLM

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


# GET 返回的 plan 携带计算字段，客户端直接回传时先剔除（extra=forbid 会拒绝）
_PLAN_COMPUTED_FIELDS = ("total_cost_cny", "per_person_cost_cny")


class UpdatePlanRequest(BaseModel):
    """手动编辑保存：提交编辑后的完整行程（骨架字段不可变）。"""

    plan: TripPlan
    reason: str | None = Field(default=None, max_length=200)

    @field_validator("plan", mode="before")
    @classmethod
    def _drop_computed_fields(cls, value: object) -> object:
        """容忍 GET → 编辑 → PUT 回传携带的计算字段（不可作为校验输入）。"""
        if isinstance(value, dict):
            return {key: item for key, item in value.items() if key not in _PLAN_COMPUTED_FIELDS}
        return value


class OptimizeRequest(BaseModel):
    """AI 优化：instruction 可空（空则只做整体排布优化）。"""

    instruction: str | None = Field(default=None, max_length=500)


class PlanMutationOut(BaseModel):
    """编辑/优化后的新版本：行程 + 版本号 + 结构化差异 + 提示（优化时）。"""

    plan: TripPlan
    plan_version: int
    diff: PlanDiff
    warnings: list[str] = Field(default_factory=list)


_TRIGGER_LABELS = {"edit:manual": "手动编辑", "edit:optimize": "AI 优化"}


def _version_snippet(row: PlanVersionRow) -> str | None:
    """版本来源可读文案：编辑/优化标记 > 差异原因 > 对话修改。"""
    if row.trigger_message_id in _TRIGGER_LABELS:
        return _TRIGGER_LABELS[row.trigger_message_id]
    if row.diff_json:
        try:
            reason = PlanDiff.model_validate_json(row.diff_json).reason
        except ValueError:
            reason = ""
        if reason:
            return reason[:120]
    return "对话修改" if row.trigger_message_id else None


@router.get("", response_model=list[PlanListOut])
async def list_my_plans(
    q: str | None = None,
    offset: int = 0,
    limit: int = 20,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    response: Response = None,  # type: ignore[assignment]  # FastAPI 注入
) -> list[PlanListOut]:
    """我的行程列表，按更新时间倒序，支持关键词搜索 + 分页。

    q 匹配标题/目的地；返回总数写入 X-Total-Count 响应头（前端分页用）。
    列表只需目的地/日期/天数：目的地与预算直接用 plans 冗余列，
    日期只做 snapshot_json 顶层的轻量 JSON 解析（不整份 Pydantic 校验，避免 N+1 全量开销）。
    """
    offset = max(0, offset)
    limit = max(1, min(limit, 100))
    repo = PlanRepository(session)
    total = await repo.count_by_user(user.id, q=q)
    rows = await repo.list_by_user(user.id, limit=limit, offset=offset, q=q)
    out: list[PlanListOut] = []
    for row in rows:
        start_date = end_date = None
        try:
            meta = _json.loads(row.snapshot_json)
            start_date = date.fromisoformat(meta["start_date"])
            end_date = date.fromisoformat(meta["end_date"])
        except (ValueError, KeyError, TypeError):
            # 快照异常时不整份解析兜底：日期留空，前端照常渲染其他列
            pass
        days = (end_date - start_date).days + 1 if start_date and end_date else 0
        out.append(
            PlanListOut(
                id=row.id,
                title=row.title,
                destination=row.destination,
                start_date=start_date.isoformat() if start_date else "",
                end_date=end_date.isoformat() if end_date else "",
                budget_cny=row.budget_cny,
                days=days,
                updated_at=row.updated_at,
                created_at=row.created_at,
            )
        )
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return out


@router.get("/{plan_id}", response_model=PlanOut)
async def get_plan(
    plan_id: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PlanOut:
    repo = PlanRepository(session)
    plan = await repo.get_plan(plan_id, user_id=user.id)
    if plan is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, f"行程 {plan_id} 不存在", status_code=404)
    thread_id = await repo.get_thread_id(plan_id, user_id=user.id)
    version = await repo.latest_version_number(plan_id)
    return PlanOut(plan=plan, plan_version=version, thread_id=thread_id)


@router.get("/{plan_id}/versions", response_model=list[VersionOut])
async def list_versions(
    plan_id: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> list[VersionOut]:
    repo = PlanRepository(session)
    plan = await repo.get_plan(plan_id, user_id=user.id)
    if plan is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, f"行程 {plan_id} 不存在", status_code=404)
    rows = await repo.list_versions(plan_id)
    return [
        VersionOut(
            version=row.version,
            trigger_snippet=_version_snippet(row),
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/{plan_id}/rollback", response_model=PlanOut)
async def rollback(
    plan_id: str,
    req: RollbackRequest,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PlanOut:
    repo = PlanRepository(session)
    plan = await repo.get_version(plan_id, req.version, user_id=user.id)
    if plan is None:
        raise AppError(ErrorCode.NOT_FOUND, f"版本 {req.version} 不存在", status_code=404)
    thread_id = await repo.get_thread_id(plan_id, user_id=user.id) or "unknown"
    # 内容去重：目标版本快照与当前/历史一致时不堆叠新版本行
    _, new_ver = await repo.save_snapshot_unique(plan, thread_id, user_id=user.id)
    return PlanOut(plan=plan, plan_version=new_ver, thread_id=thread_id)


@router.put("/{plan_id}", response_model=PlanMutationOut)
async def update_plan(
    plan_id: str,
    req: UpdatePlanRequest,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PlanMutationOut:
    """手动编辑保存：骨架保护 + 事实字段校验后写新版本（差异写入版本历史）。"""
    repo = PlanRepository(session)
    current = await repo.get_plan(plan_id, user_id=user.id)
    if current is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, f"行程 {plan_id} 不存在", status_code=404)
    try:
        edited = apply_manual_edit(current, req.plan)
    except ValueError as exc:
        raise AppError(ErrorCode.BAD_REQUEST, str(exc), status_code=422) from exc
    version = await repo.latest_version_number(plan_id)
    if edited == current:  # 编辑未产生任何变化：不写空版本，直接返回
        return PlanMutationOut(plan=current, plan_version=version, diff=PlanDiff())
    diff = compute_diff(current, edited, reason=req.reason or "手动编辑")
    thread_id = await repo.get_thread_id(plan_id, user_id=user.id) or "unknown"
    _, new_ver = await repo.save_snapshot_unique(
        edited,
        thread_id,
        diff_json=diff.model_dump_json(),
        trigger_message_id="edit:manual",
        user_id=user.id,
    )
    return PlanMutationOut(plan=edited, plan_version=new_ver, diff=diff)


@router.post("/{plan_id}/optimize", response_model=PlanMutationOut)
async def optimize_plan(
    plan_id: str,
    req: OptimizeRequest,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    llm: StructuredLLM | None = Depends(get_llm),
    ctx: ToolRegistry = Depends(get_tool_context),
) -> PlanMutationOut:
    """AI 优化：对当前存档整体重排（可附优化说明），产出实时最新方案并写新版本。"""
    if llm is None:
        raise AppError(
            ErrorCode.LLM_NOT_CONFIGURED,
            "未配置 LLM_API_KEY，无法执行 AI 优化",
            status_code=503,
        )
    repo = PlanRepository(session)
    current = await repo.get_plan(plan_id, user_id=user.id)
    if current is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, f"行程 {plan_id} 不存在", status_code=404)
    try:
        result = await optimize_saved_plan(
            current, req.instruction or "", llm=llm, settings=settings, ctx=ctx
        )
    except LLMError as exc:
        raise AppError(ErrorCode.UPSTREAM_ERROR, f"AI 优化失败：{exc}", status_code=502) from exc
    thread_id = await repo.get_thread_id(plan_id, user_id=user.id) or "unknown"
    _, new_ver = await repo.save_snapshot_unique(
        result.plan,
        thread_id,
        diff_json=result.diff.model_dump_json(),
        trigger_message_id="edit:optimize",
        user_id=user.id,
    )
    return PlanMutationOut(
        plan=result.plan,
        plan_version=new_ver,
        diff=result.diff,
        warnings=list(result.warnings),
    )


@router.post("", response_model=PlanOut)
async def create_plan(
    req: CreatePlanRequest,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PlanOut:
    """手工新建行程，占位：仅创建空骨架，前端跳编辑页补全。"""
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
    repo = PlanRepository(session)
    await repo.save_snapshot(plan, uuid.uuid4().hex, 1, user_id=user.id)
    return PlanOut(plan=plan, plan_version=1, thread_id=None)


@router.post("/{plan_id}/clone", response_model=PlanOut)
async def clone_plan(
    plan_id: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> PlanOut:
    """克隆行程：复制当前最新快照生成一份全新独立行程（新 id、版本从 1 开始）。"""
    repo = PlanRepository(session)
    plan = await repo.get_plan(plan_id, user_id=user.id)
    if plan is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, f"行程 {plan_id} 不存在", status_code=404)
    new_id = new_plan_id()
    # model_copy 深拷贝全部字段，仅替换 plan_id（来源 URL 等保持原样，视为用户自留资料）
    cloned = plan.model_copy(deep=True, update={"plan_id": new_id})
    await repo.save_snapshot(cloned, uuid.uuid4().hex, 1, user_id=user.id)
    return PlanOut(plan=cloned, plan_version=1, thread_id=None)


@router.delete("/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> None:
    """删除行程及其所有版本；非本人返回 404。"""
    repo = PlanRepository(session)
    ok = await repo.delete_plan(plan_id, user.id)
    if not ok:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, f"行程 {plan_id} 不存在", status_code=404)


@router.post("/{plan_id}/pdf")
async def export_pdf(
    plan_id: str,
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """PDF 导出：渲染打印版 HTML（Jinja2 服务端）→ Playwright 无头 Chromium 转 A4 PDF。"""
    from travel_agent.services.pdf import PdfUnavailable, render_plan_pdf

    repo = PlanRepository(session)
    plan = await repo.get_plan(plan_id, user_id=user.id)
    if plan is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, f"行程 {plan_id} 不存在", status_code=404)
    version = await repo.latest_version_number(plan_id)
    try:
        pdf_bytes = await render_plan_pdf(plan, plan_version=version)
    except PdfUnavailable as exc:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, str(exc), status_code=503) from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{plan_id}.pdf"',
            "Cache-Control": "no-store",
        },
    )
