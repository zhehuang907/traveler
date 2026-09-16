"""answer_info 节点：ask_info 意图的检索增强。

问询类问题（如「成都三天怎么玩」「带老人去重庆注意什么」）先做网页检索，
把真实资料格式化进 reply_hint，再由 respond 节点基于资料拆解问题、
给出结构化建议。工具失败不炸图（search_web 内部已降级为空列表）。
"""

import asyncio

from langchain_core.messages import HumanMessage

from travel_agent.agent.planning import format_web
from travel_agent.agent.state import TravelState
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.agent.tools.web_search import search_web


def build_queries(question: str, destination: str) -> list[str]:
    """根据问题构造 1-2 条检索词：目的地明确时带上城市限定。"""
    q = question.strip()
    if destination:
        return [
            f"{destination} {q} 攻略 建议",
            f"{destination} 旅游 {q} 注意事项",
        ]
    return [f"{q} 旅游 攻略 建议"]


async def answer_info(state: TravelState, *, ctx: ToolRegistry) -> dict[str, object]:
    """检索与问题相关的真实资料，写入 reply_hint 供 respond 使用。"""
    question = _last_human_text(state)
    brief = state.get("brief")
    destination = brief.destination if brief is not None and brief.destination else ""

    queries = build_queries(question, destination)
    results = await asyncio.gather(
        *[search_web(ctx, query, max_results=5, freshness="") for query in queries]
    )
    # 合并去重（按 URL）
    seen: set[str] = set()
    merged = []
    for group in results:
        for item in group:
            key = str(item.url)
            if key not in seen:
                seen.add(key)
                merged.append(item)

    hint = format_web(merged[:8])
    if not hint:
        hint = "（未检索到相关资料：请基于常识谨慎回答，不确定的内容明确告知用户）"
    return {
        "reply_hint": hint,
        "web_results": merged[:8],
        "traces": ctx.trace_snapshot(),
    }


def _last_human_text(state: TravelState) -> str:
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""
