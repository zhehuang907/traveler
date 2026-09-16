"""respond 节点：把最终行程或问答结果渲染成用户可读回复。"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from travel_agent.agent.prompts import render_pair
from travel_agent.agent.state import TravelState, weather_by_date
from travel_agent.config import Settings
from travel_agent.domain.diff import PlanDiff
from travel_agent.domain.rules import extreme_weather_alerts
from travel_agent.services.llm import StructuredLLM


async def respond(
    state: TravelState, *, llm: StructuredLLM, settings: Settings
) -> dict[str, object]:
    if state.get("plan") is not None:
        system, user = _plan_prompt(state, settings)
    else:
        system, user = _chat_prompt(state)
    reply = await llm.acomplete(system=system, user=user)
    return {"reply": reply, "messages": [AIMessage(content=reply)]}


def _plan_prompt(state: TravelState, settings: Settings) -> tuple[str, str]:
    plan = state.get("plan")
    if plan is None:  # 防御：只有存在行程才走 plan 分支
        raise RuntimeError("respond 的 plan 模式需要已生成的行程")
    forced = state.get("loop_count", 0) >= settings.max_revise_loops
    alerts = extreme_weather_alerts(weather_by_date(state))
    warnings = state.get("hydration_warnings", [])
    diff = state.get("plan_diff")
    return render_pair(
        "respond",
        mode="plan",
        plan_json=plan.model_dump_json(),
        warnings_text="\n".join(f"- {item}" for item in warnings) if warnings else "无",
        alerts_text="\n".join(f"- {item}" for item in alerts) if alerts else "无",
        unresolved=state.get("reflections", []) if forced else [],
        diff_text=_format_diff(diff) if diff else "",
    )


def _chat_prompt(state: TravelState) -> tuple[str, str]:
    hint = state.get("reply_hint", "")
    return render_pair(
        "respond",
        mode="chat",
        transcript=_transcript(state),
        hint=hint or "（无额外素材）",
        message=_last_human_text(state),
    )


def _transcript(state: TravelState, limit: int = 6) -> str:
    messages = state.get("messages", [])[-limit:]
    lines = [
        f"{'用户' if isinstance(m, HumanMessage) else '助手'}：{_content(m)}" for m in messages
    ]
    return "\n".join(lines)


def _content(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content)


def _last_human_text(state: TravelState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return _content(message)
    return ""


def _format_diff(diff: PlanDiff) -> str:
    """把 PlanDiff 渲染为中文变更说明（注入 respond 模板）。"""
    lines: list[str] = []
    if diff.reason:
        lines.append(f"修改范围：{diff.reason}")
    for entry in diff.added:
        lines.append(f"新增 第{entry.day}天 {entry.title}")
    for entry in diff.removed:
        lines.append(f"删除 第{entry.day}天 {entry.title}")
    for change in diff.changed:
        lines.append(
            f"变更 第{change.day}天 {change.item_id} {change.field}: "
            f"{change.before} → {change.after}"
        )
    return "\n".join(lines) if lines else "无变更"
