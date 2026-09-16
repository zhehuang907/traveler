"""patch_plan 节点：按 target_scope 定向修改既有行程，保持 plan_id 稳定。

与 revise_plan 的区别：
- revise 驱动来自程序化 reflections（校验失败），面向全量重排；
- patch 驱动来自用户自然语言修改要求（target_scope），面向定向调整，
  未被点名的天/项由提示词约束原样保留，差异由 compute_diff 精确记录。
"""

from langchain_core.messages import HumanMessage

from travel_agent.agent.nodes.compose import planning_context
from travel_agent.agent.planning import format_plan_with_refs
from travel_agent.agent.prompts import render_pair
from travel_agent.agent.state import TravelState, weather_by_date
from travel_agent.config import Settings
from travel_agent.domain.diff import compute_diff
from travel_agent.domain.draft import PlanDraft
from travel_agent.domain.plan_builder import hydrate_plan
from travel_agent.services.llm import StructuredLLM


async def patch_plan(
    state: TravelState, *, llm: StructuredLLM, settings: Settings
) -> dict[str, object]:
    current = state["plan"]
    if current is None:  # 防御：路由保证 modify_plan 时已有行程
        raise RuntimeError("patch_plan 在无既有行程时被调用")

    scope = state.get("target_scope", [])
    context = planning_context(state, settings)
    catalog = context.pop("_catalog")
    context["plan_text"] = format_plan_with_refs(current, catalog)
    context["target_scope_text"] = "、".join(scope) if scope else "未指定（按自然语言理解）"
    context["user_instruction"] = _last_human_text(state)

    system, user = render_pair("patch", **context)
    draft = await llm.aparse(PlanDraft, system=system, user=user)
    result = hydrate_plan(
        draft,
        state["brief"],
        catalog,
        weather_by_date(state),
        state.get("city_location"),
    )
    # patch 不换行程身份：plan_id 保持稳定
    new_plan = result.plan.model_copy(update={"plan_id": current.plan_id})
    diff = compute_diff(current, new_plan, reason="; ".join(scope) if scope else "")
    return {
        "plan": new_plan,
        "plan_diff": diff,
        "hydration_warnings": list(result.warnings),
        "plan_version": state.get("plan_version", 1) + 1,
        "reflections": [],
        "loop_count": 0,
    }


def _last_human_text(state: TravelState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""
