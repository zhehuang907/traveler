"""clarify_brief 节点：信息不全时输出唯一一个聚合追问。"""

from langchain_core.messages import AIMessage, HumanMessage

from travel_agent.agent.prompts import render_pair
from travel_agent.agent.state import TravelState
from travel_agent.services.llm import StructuredLLM


async def clarify_brief(state: TravelState, *, llm: StructuredLLM) -> dict[str, object]:
    brief = state.get("brief")
    labels = brief.missing_slot_labels() if brief else []
    preference_hints = brief.unanswered_preference_labels() if brief else []
    summary = brief.summary_text() if brief else "（暂无）"
    message = _last_human_text(state)
    system, user = render_pair(
        "clarify",
        missing_labels=labels,
        preference_hints=preference_hints,
        destination=brief.destination if brief else "",
        summary=summary,
        message=message,
    )
    reply = await llm.acomplete(system=system, user=user)
    return {"reply": reply, "messages": [AIMessage(content=reply)]}


def _last_human_text(state: TravelState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""
