"""LangGraph 状态图装配（Prompt 4.2 节点顺序 + 校验/修订条件循环）。"""

from collections.abc import Callable
from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from travel_agent.agent.nodes.answer_info import answer_info
from travel_agent.agent.nodes.clarify import clarify_brief
from travel_agent.agent.nodes.compose import compose_plan
from travel_agent.agent.nodes.parse_intent import parse_intent
from travel_agent.agent.nodes.patch import patch_plan
from travel_agent.agent.nodes.respond import respond
from travel_agent.agent.nodes.revise import revise_plan
from travel_agent.agent.nodes.search import gather_candidates
from travel_agent.agent.nodes.validate import validate_plan
from travel_agent.agent.nodes.weather import fetch_weather
from travel_agent.agent.state import TravelState
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.config import Settings
from travel_agent.services.llm import StructuredLLM

Node = Callable[[TravelState], Any]


def route_after_intent(state: TravelState) -> str:
    """意图后路由：修改→补丁；问询→检索增强；闲聊→回应；需求不全→追问；齐备→检索。"""
    intent = state.get("intent", "chitchat")
    if intent == "modify_plan":
        return "patch_plan"
    if intent == "ask_info":
        return "answer_info"
    if intent == "chitchat":
        return "respond"
    brief = state.get("brief")
    return "search" if brief is not None and brief.is_ready() else "clarify_brief"


def route_after_validate(state: TravelState, max_loops: int) -> str:
    """校验后路由：无反馈交付；超限强制交付并提示；否则带反馈修订。"""
    if not state.get("reflections") or state.get("loop_count", 0) >= max_loops:
        return "respond"
    return "revise_plan"


def build_graph(
    *,
    settings: Settings,
    llm: StructuredLLM,
    ctx: ToolRegistry,
    checkpointer: Any = None,
) -> Any:
    """编译旅行规划图；checkpointer 为 None 时图不可跨进程恢复（测试用）。"""
    graph = StateGraph(TravelState)
    graph.add_node("parse_intent", partial(parse_intent, llm=llm))
    graph.add_node("clarify_brief", partial(clarify_brief, llm=llm))
    graph.add_node("search", partial(gather_candidates, ctx=ctx))
    graph.add_node("weather", partial(fetch_weather, ctx=ctx))
    graph.add_node("compose_plan", partial(compose_plan, llm=llm, settings=settings))
    graph.add_node("validate_plan", partial(validate_plan, ctx=ctx, settings=settings))
    graph.add_node("revise_plan", partial(revise_plan, llm=llm, settings=settings))
    graph.add_node("respond", partial(respond, llm=llm, settings=settings))
    graph.add_node("patch_plan", partial(patch_plan, llm=llm, settings=settings))
    graph.add_node("answer_info", partial(answer_info, ctx=ctx))

    graph.add_edge(START, "parse_intent")
    graph.add_conditional_edges(
        "parse_intent",
        route_after_intent,
        {
            "respond": "respond",
            "search": "search",
            "clarify_brief": "clarify_brief",
            "patch_plan": "patch_plan",
            "answer_info": "answer_info",
        },
    )
    graph.add_edge("clarify_brief", END)
    graph.add_edge("answer_info", "respond")
    graph.add_edge("search", "weather")
    graph.add_edge("weather", "compose_plan")
    graph.add_edge("compose_plan", "validate_plan")
    graph.add_edge("patch_plan", "validate_plan")
    graph.add_conditional_edges(
        "validate_plan",
        lambda state: route_after_validate(state, settings.max_revise_loops),
        {"respond": "respond", "revise_plan": "revise_plan"},
    )
    graph.add_edge("revise_plan", "validate_plan")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)
