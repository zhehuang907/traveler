"""revise_plan 节点：带着程序化 reflections 重排，保持 plan_id 稳定。"""

from travel_agent.agent.nodes.compose import planning_context
from travel_agent.agent.planning import format_plan_with_refs
from travel_agent.agent.prompts import render_pair
from travel_agent.agent.state import TravelState, weather_by_date
from travel_agent.config import Settings
from travel_agent.domain.draft import PlanDraft
from travel_agent.domain.plan_builder import hydrate_plan
from travel_agent.services.llm import StructuredLLM


async def revise_plan(
    state: TravelState, *, llm: StructuredLLM, settings: Settings
) -> dict[str, object]:
    current = state["plan"]
    if current is None:  # 防御：路由保证不会发生
        raise RuntimeError("revise_plan 在无既有行程时被调用")
    context = planning_context(state, settings)
    catalog = context.pop("_catalog")
    context["plan_json"] = format_plan_with_refs(current, catalog)
    context["reflections"] = state.get("reflections", [])
    system, user = render_pair("revise", **context)
    draft = await llm.aparse(PlanDraft, system=system, user=user)
    result = hydrate_plan(
        draft,
        state["brief"],
        catalog,
        weather_by_date(state),
        state.get("city_location"),
    )
    # 修订不换行程身份：版本链在阶段四依赖稳定 plan_id
    plan = result.plan.model_copy(update={"plan_id": current.plan_id})
    return {
        "plan": plan,
        "hydration_warnings": list(result.warnings),
        "plan_version": state.get("plan_version", 1) + 1,
        "reflections": [],
    }
