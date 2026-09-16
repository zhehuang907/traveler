"""LangGraph 全局状态定义（Prompt 4.1）。

纪律：
- 状态中只放 Pydantic 领域模型、langchain 消息与 JSON 原生值；
  checkpointer 使用 agent.serde 的 TypedJSON 序列化（dataclass/元组不入状态）；
- ``weather`` 以 ISO 日期字符串为键（JSON 友好），节点使用前用
  ``weather_by_date`` 还原成 ``date`` 键映射供领域规则消费。
"""

from datetime import date
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.diff import PlanDiff
from travel_agent.domain.models import DailyWeather, DomainModel, GeoPoint, Poi, SearchResult
from travel_agent.domain.plan import TripPlan

Intent = Literal["new_plan", "modify_plan", "ask_info", "chitchat"]

# 候选分组键：attractions / restaurants / hotels
CandidateGroups = dict[str, list[Poi]]


class ToolTrace(DomainModel):
    """一次工具调用的 trace（成本观测 + 降级标记）。"""

    tool: str
    ok: bool
    degraded: bool = False
    detail: str = ""


class TravelState(TypedDict, total=False):
    """图运行期状态；通道全部可选，节点按需返回增量。"""

    messages: Annotated[list[BaseMessage], add_messages]
    brief: TravelBrief
    intent: Intent
    # 修改类请求命中的天/项（阶段三仅识别，阶段四 Patch 使用）
    target_scope: list[str]
    candidates: CandidateGroups
    web_results: list[SearchResult]
    # ISO 日期字符串 -> 逐日天气
    weather: dict[str, DailyWeather]
    city_location: GeoPoint | None
    plan: TripPlan | None
    plan_diff: PlanDiff | None
    hydration_warnings: list[str]
    plan_version: int
    reflections: list[str]
    loop_count: int
    traces: list[ToolTrace]
    reply_hint: str
    reply: str


def weather_by_date(state: TravelState) -> dict[date, DailyWeather]:
    """把状态中的 ISO 键天气映射还原为 date 键（供 rules 消费）。"""
    return {date.fromisoformat(day): daily for day, daily in state.get("weather", {}).items()}
