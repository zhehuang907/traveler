"""compose_plan 节点：候选+天气 → LLM 草稿 → 确定性水合成 TripPlan。"""

from typing import Any

from travel_agent.agent.planning import build_catalog, format_catalog, format_weather, format_web
from travel_agent.agent.prompts import render_pair
from travel_agent.agent.state import TravelState, weather_by_date
from travel_agent.config import Settings
from travel_agent.domain.draft import PlanDraft
from travel_agent.domain.plan_builder import hydrate_plan
from travel_agent.services.llm import StructuredLLM


def planning_context(state: TravelState, settings: Settings) -> dict[str, Any]:
    """compose/revise 共用的模板上下文（不含 plan/reflections）。"""
    brief = state["brief"]
    catalog = build_catalog(state.get("candidates", {}))
    budget = "不限" if brief.budget_cny is None else f"{brief.budget_cny:g}"
    return {
        "days": brief.duration_days,
        "budget": budget,
        "max_daily_hours": settings.max_daily_hours,
        "brief_json": brief.model_dump_json(),
        "weather_text": format_weather(state.get("weather", {})),
        "catalog_text": format_catalog(catalog),
        "web_text": format_web(state.get("web_results", [])),
        "_catalog": catalog,
    }


async def compose_plan(
    state: TravelState, *, llm: StructuredLLM, settings: Settings
) -> dict[str, object]:
    context = planning_context(state, settings)
    catalog = context.pop("_catalog")
    system, user = render_pair("compose", **context)
    draft = await llm.aparse(PlanDraft, system=system, user=user)
    result = hydrate_plan(
        draft,
        state["brief"],
        catalog,
        weather_by_date(state),
        state.get("city_location"),
    )
    return {
        "plan": result.plan,
        "hydration_warnings": list(result.warnings),
        "plan_version": 1,
        "reflections": [],
        "loop_count": 0,
    }
