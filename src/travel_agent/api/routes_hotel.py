"""酒店推荐接口。

两个端点
--------
- ``GET /api/plan/{plan_id}/hotels``          —— 平铺列表（按距离/关键词），
  适合「看看附近有哪些酒店」的浏览场景。
- ``GET /api/plan/{plan_id}/hotels/tiered``  —— 性价比/轻奢/高奢三档各给
  最优候选，适合「帮我挑一家」的决策场景。

共同设计
--------
- 端点挂在**计划**下，因为人数/日期/城市都来自计划本身，用户不应再手填一遍。
- 允许覆写人数与日期，便于「按 3 人临时算一下」这类需求；不传则用计划里的值。
- 距离排序锚点取计划中**第一个有坐标的景点**；计划无坐标时退回按城市检索。
- 档次与价格都**如实标注可信等级**，不用估算值冒充实时报价。
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from travel_agent.api.deps import get_current_user, get_db, get_settings
from travel_agent.api.errors import AppError, ErrorCode
from travel_agent.config import Settings
from travel_agent.db import PlanRepository
from travel_agent.db.models import UserRow
from travel_agent.domain.models import (
    GeoPoint,
    HotelRecommendation,
    TieredHotelRecommendation,
)
from travel_agent.services.hotels import HotelService

__all__ = ["router"]

router = APIRouter(prefix="/api/plan", tags=["hotel"])

#: 单次返回条数上限
_MAX_LIMIT = 12


@router.get("/{plan_id}/hotels", response_model=HotelRecommendation)
async def recommend_hotels(
    plan_id: str,
    travelers: int | None = Query(
        default=None,
        ge=1,
        le=50,
        description="出行人数（含儿童），缺省用计划值；用于推断房型",
    ),
    check_in: date | None = Query(default=None, description="入住日期，缺省用计划开始日"),
    check_out: date | None = Query(default=None, description="离店日期，缺省用计划结束日"),
    keyword: str = Query(default="", max_length=30, description="偏好关键词，如「亲子」"),
    limit: int = Query(default=8, ge=1, le=_MAX_LIMIT, description="返回条数"),
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> HotelRecommendation:
    """按计划人数与日期推荐酒店，并给出房型建议。

    价格说明：当前价格来源为高德参考均价（非指定日期实时房价），
    响应里的 ``disclaimer`` 会明确告知用户实际价格以下单平台为准。
    """
    repo = PlanRepository(session)
    plan = await repo.get_plan(plan_id, user_id=user.id)
    if plan is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, "行程不存在或无权访问", status_code=404)

    people = travelers if travelers is not None else plan.travelers
    ci = check_in or plan.start_date
    co = check_out or plan.end_date
    _ensure_lodging_nights(ci, co)

    service = HotelService(settings)
    try:
        return await service.recommend(
            plan.destination,
            travelers=people,
            check_in=ci,
            check_out=co,
            near=_anchor_point(plan),
            keyword=keyword,
            limit=limit,
        )
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc), status_code=400) from exc
    except Exception as exc:
        # 外部服务全失败：不返回空列表冒充「没有酒店」，而是明确报错
        raise AppError(
            ErrorCode.UPSTREAM_ERROR,
            f"酒店数据获取失败：{type(exc).__name__}",
            status_code=502,
        ) from exc


@router.get("/{plan_id}/hotels/tiered", response_model=TieredHotelRecommendation)
async def recommend_hotels_by_tier(
    plan_id: str,
    travelers: int | None = Query(
        default=None,
        ge=1,
        le=50,
        description="出行人数（含儿童），缺省用计划值；用于推断房型",
    ),
    check_in: date | None = Query(default=None, description="入住日期，缺省用计划开始日"),
    check_out: date | None = Query(default=None, description="离店日期，缺省用计划结束日"),
    per_tier: int = Query(default=1, ge=1, le=3, description="每档返回条数"),
    user: UserRow = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TieredHotelRecommendation:
    """按「性价比 / 轻奢 / 高奢」三档各给出最合适的酒店。

    档次依据高德住宿星级类目判定。某档在目的地确实没有时返回空列表，
    不用低档酒店冒充高档。
    """
    repo = PlanRepository(session)
    plan = await repo.get_plan(plan_id, user_id=user.id)
    if plan is None:
        raise AppError(ErrorCode.PLAN_NOT_FOUND, "行程不存在或无权访问", status_code=404)

    people = travelers if travelers is not None else plan.travelers
    ci = check_in or plan.start_date
    co = check_out or plan.end_date
    _ensure_lodging_nights(ci, co)

    service = HotelService(settings)
    try:
        return await service.recommend_tiers(
            plan.destination,
            travelers=people,
            check_in=ci,
            check_out=co,
            near=_anchor_point(plan),
            per_tier=per_tier,
        )
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION_ERROR, str(exc), status_code=400) from exc
    except Exception as exc:
        raise AppError(
            ErrorCode.UPSTREAM_ERROR,
            f"酒店数据获取失败：{type(exc).__name__}",
            status_code=502,
        ) from exc


def _ensure_lodging_nights(check_in: date, check_out: date) -> None:
    """校验住宿晚数。计划是当日往返（一日游）时如实报错，不编造报价。"""
    if check_out <= check_in:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "该行程为当日往返，无住宿需求；如需住宿请延长行程或指定离店日期",
            status_code=400,
        )


def _anchor_point(plan: object) -> GeoPoint | None:
    """取计划中第一个有坐标的景点作为距离排序锚点。"""
    destination = getattr(plan, "city_location", None)
    if isinstance(destination, GeoPoint):
        return destination
    days = getattr(plan, "days", None) or []
    for day in days:
        for item in getattr(day, "items", None) or []:
            location = getattr(item, "location", None)
            if isinstance(location, GeoPoint):
                return location
    return None
