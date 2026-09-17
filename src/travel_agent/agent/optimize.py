"""已存档行程的 AI 优化编排（单次 LLM 调用，同步返回新版本，不落库）。

与图内 patch 节点同源：LLM 只按候选编号「点菜」，事实字段由
``hydrate_plan`` 水合。候选目录 = 既有行程反构 +（说明命中类目时）
补充检索，补充查询与初始规划一致，命中 24h POI 缓存几乎零额外开销；
天气获取失败与检索失败一律降级为「无该信息」，不允许炸断优化流程。
"""

from dataclasses import dataclass
from datetime import date

from travel_agent.agent.planning import format_catalog, format_plan_with_refs, format_weather
from travel_agent.agent.prompts import render_pair
from travel_agent.agent.tools import search_hotel, search_poi
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.config import Settings
from travel_agent.domain.diff import PlanDiff, compute_diff
from travel_agent.domain.draft import PlanDraft
from travel_agent.domain.models import DailyWeather, Poi
from travel_agent.domain.plan import TripPlan
from travel_agent.domain.plan_builder import hydrate_plan
from travel_agent.domain.plan_edit import brief_from_plan, extend_catalog, rebuild_catalog_from_plan
from travel_agent.domain.rules import evaluate_plan
from travel_agent.services.llm import StructuredLLM

# 优化说明命中的类目关键词 -> 触发对应补充检索（复用初始规划同款查询以命中缓存）
_EXTRA_GRID: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hotel", ("酒店", "住宿", "民宿", "宾馆")),
    ("attraction", ("景点", "游玩", "景区", "博物馆", "公园")),
    ("restaurant", ("餐厅", "美食", "火锅", "小吃")),
)


@dataclass(frozen=True)
class OptimizeResult:
    """一次优化的产出：新版本行程 + 结构化差异 + 提示与新增候选数。"""

    plan: TripPlan
    diff: PlanDiff
    warnings: tuple[str, ...]
    extra_candidates: int


def detect_extra_groups(instruction: str) -> list[str]:
    """从优化说明中识别需要补充检索的候选类目。"""
    return [group for group, words in _EXTRA_GRID if any(word in instruction for word in words)]


async def optimize_saved_plan(
    current: TripPlan,
    instruction: str,
    *,
    llm: StructuredLLM,
    settings: Settings,
    ctx: ToolRegistry,
) -> OptimizeResult:
    """在既有行程上执行整体优化，返回新版本（plan_id 保持稳定）。"""
    catalog = rebuild_catalog_from_plan(current)
    base_count = len(catalog.entries)
    if instruction.strip():
        extras = await _fetch_extra_candidates(current, instruction, ctx)
        catalog = extend_catalog(catalog, extras)
    weather = await _fetch_weather(current, ctx)

    brief = brief_from_plan(current)
    context = {
        "days": (current.end_date - current.start_date).days + 1,
        "budget": "不限" if current.budget_cny is None else f"{current.budget_cny:g}",
        "max_daily_hours": settings.max_daily_hours,
        "brief_json": brief.model_dump_json(),
        "plan_text": format_plan_with_refs(current, catalog),
        "catalog_text": format_catalog(catalog),
        "weather_text": format_weather({day.isoformat(): daily for day, daily in weather.items()}),
        "instruction": instruction.strip() or "（未提供额外说明：请做整体排布优化）",
    }
    system, user = render_pair("optimize", **context)
    draft = await llm.aparse(PlanDraft, system=system, user=user)

    hydrated = hydrate_plan(draft, brief, catalog, weather, current.city_location)
    new_plan = hydrated.plan.model_copy(update={"plan_id": current.plan_id})
    # 轻量规则自检（无 IO 部分）：提醒回传，不进入图内 revise 循环
    reflections = evaluate_plan(
        new_plan, weather, catalog.business_hours(), {}, settings.max_daily_hours
    )
    diff = compute_diff(current, new_plan, reason=instruction.strip() or "AI 优化")
    return OptimizeResult(
        plan=new_plan,
        diff=diff,
        warnings=tuple(hydrated.warnings) + tuple(reflections),
        extra_candidates=len(catalog.entries) - base_count,
    )


async def _fetch_extra_candidates(
    current: TripPlan, instruction: str, ctx: ToolRegistry
) -> dict[str, list[Poi]]:
    """按说明中的类目关键词补充检索候选；工具失败降级为空（不炸断优化）。"""
    groups = detect_extra_groups(instruction)
    extras: dict[str, list[Poi]] = {}
    if "hotel" in groups:
        extras["hotel"] = await search_hotel(
            ctx, current.destination, current.start_date, current.end_date, current.budget_cny
        )
    if "attraction" in groups:
        extras["attraction"] = await search_poi(
            ctx, current.destination, f"{current.destination}必去景点 排名", limit=10
        )
    if "restaurant" in groups:
        extras["restaurant"] = await search_poi(
            ctx, current.destination, f"{current.destination}特色美食 推荐餐厅", limit=8
        )
    return extras


async def _fetch_weather(current: TripPlan, ctx: ToolRegistry) -> dict[date, DailyWeather]:
    """尽力获取行程窗口天气；失败降级为空（优化不因天气不可用而失败）。"""
    try:
        forecast = await ctx.weather.get_weather(
            current.destination, current.start_date, current.end_date
        )
    except Exception:
        return {}
    return {daily.date: daily for daily in forecast.days}
