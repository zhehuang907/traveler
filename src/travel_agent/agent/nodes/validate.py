"""validate_plan 节点：先核实相邻通勤，再跑全部程序化规则（无 LLM 判断）。"""

import asyncio

from travel_agent.agent.planning import build_catalog
from travel_agent.agent.state import TravelState, weather_by_date
from travel_agent.agent.tools import get_route
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.config import Settings
from travel_agent.domain.models import GeoPoint, RouteInfo, TravelMode
from travel_agent.domain.plan import PlanDay, TripPlan
from travel_agent.domain.rules import evaluate_plan


async def validate_plan(
    state: TravelState, *, ctx: ToolRegistry, settings: Settings
) -> dict[str, object]:
    plan = state["plan"]
    if plan is None:  # 防御：路由保证不会发生
        raise RuntimeError("validate_plan 在无行程时被调用")
    commute = await _commute_minutes(ctx, plan)
    catalog = build_catalog(state.get("candidates", {}))
    reflections = evaluate_plan(
        plan,
        weather_by_date(state),
        catalog.business_hours(),
        commute,
        settings.max_daily_hours,
    )
    return {
        "reflections": reflections,
        "loop_count": state.get("loop_count", 0) + 1,
        "traces": ctx.trace_snapshot(),
    }


def _day_pairs(day: PlanDay) -> list[tuple[str, str, GeoPoint, GeoPoint, TravelMode]]:
    pairs: list[tuple[str, str, GeoPoint, GeoPoint, TravelMode]] = []
    for earlier, later in zip(day.items, day.items[1:], strict=False):
        origin = earlier.location
        destination = later.location
        if origin is None or destination is None:
            continue
        mode: TravelMode = earlier.travel_mode_to_next or "driving"
        pairs.append((earlier.item_id, later.item_id, origin, destination, mode))
    return pairs


async def _commute_minutes(ctx: ToolRegistry, plan: TripPlan) -> dict[tuple[str, str], int]:
    jobs = [pair for day in plan.days for pair in _day_pairs(day)]
    routes = await asyncio.gather(
        *[get_route(ctx, origin, destination, mode) for _, _, origin, destination, mode in jobs]
    )
    minutes: dict[tuple[str, str], int] = {}
    for (first_id, second_id, _, _, _), route in zip(jobs, routes, strict=True):
        if isinstance(route, RouteInfo):
            minutes[(first_id, second_id)] = route.duration_s // 60
    return minutes
