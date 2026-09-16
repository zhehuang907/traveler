"""parse_intent 节点：意图判定 + 槽位抽取/合并（结构化输出）。"""

from datetime import date

from langchain_core.messages import BaseMessage, HumanMessage

from travel_agent.agent.contracts import IntentResult
from travel_agent.agent.prompts import render_pair
from travel_agent.agent.state import TravelState
from travel_agent.domain.brief import TravelBrief
from travel_agent.services.llm import StructuredLLM

_WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")


async def parse_intent(state: TravelState, *, llm: StructuredLLM) -> dict[str, object]:
    message = _last_human_text(state)
    today = date.today()
    existing = state.get("brief") or TravelBrief()
    system, user = render_pair(
        "intent",
        today=today.isoformat(),
        weekday=_WEEKDAYS[today.weekday()],
        brief_json=existing.model_dump_json(),
        message=message,
    )
    result = await llm.aparse(
        IntentResult,
        system=system,
        user=user,
    )
    return {
        "intent": result.intent,
        "brief": result.brief,
        "target_scope": result.target_scope,
        "reply_hint": result.reply_hint,
    }


def _last_human_text(state: TravelState) -> str:
    messages = state.get("messages", [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    contents = [str(m.content) for m in messages if isinstance(m, BaseMessage)]
    return " ".join(contents).strip()
