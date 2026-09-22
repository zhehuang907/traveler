"""parse_intent 节点：意图判定 + 槽位抽取/合并（会话历史视角）。"""

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
        transcript=_transcript(state),
        message=message,
    )
    result = await llm.aparse(
        IntentResult,
        system=system,
        user=user,
    )
    # 确定性合并兜底：LLM 漏掉/置空的历史槽位用已有值保住，只会越滚越全
    merged = existing.merge(result.brief)
    # 规划意图下未指定出行方式 → 按业务规则默认公共交通（自驾需用户明确表达）
    if result.intent == "new_plan" and merged.transport is None:
        merged = merged.model_copy(update={"transport": "public"})
    return {
        "intent": result.intent,
        "brief": merged,
        "target_scope": result.target_scope,
        "reply_hint": result.reply_hint,
    }


def _transcript(state: TravelState, limit: int = 6) -> str:
    """最近几轮对话原文，供 LLM 从会话上下文中检索已提供过的信息。"""
    messages = state.get("messages", [])[-limit:]
    lines = [
        f"{'用户' if isinstance(m, HumanMessage) else '助手'}：{_content(m)}" for m in messages
    ]
    return "\n".join(lines) or "（无）"


def _content(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content)


def _last_human_text(state: TravelState) -> str:
    messages = state.get("messages", [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    contents = [str(m.content) for m in messages if isinstance(m, BaseMessage)]
    return " ".join(contents).strip()
