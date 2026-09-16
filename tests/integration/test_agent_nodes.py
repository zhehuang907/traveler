"""8 个图节点的独立测试（FakeLLM + 内存假服务，无网络）。"""

from datetime import date
from typing import cast

import pytest
from agent_fakes import (
    FakeLLM,
    FakeMaps,
    FakeWeather,
    chengdu_brief,
    chengdu_candidates,
    forecast_chengdu,
    valid_draft,
)
from langchain_core.messages import AIMessage, HumanMessage

from travel_agent.agent.contracts import IntentResult
from travel_agent.agent.nodes.clarify import clarify_brief
from travel_agent.agent.nodes.compose import compose_plan
from travel_agent.agent.nodes.parse_intent import parse_intent
from travel_agent.agent.nodes.patch import patch_plan
from travel_agent.agent.nodes.respond import _plan_prompt, respond
from travel_agent.agent.nodes.revise import revise_plan
from travel_agent.agent.nodes.search import (
    _sort_results,
    attraction_queries,
    gather_candidates,
    restaurant_queries,
)
from travel_agent.agent.nodes.validate import _day_pairs, validate_plan
from travel_agent.agent.nodes.weather import fetch_weather
from travel_agent.agent.state import (
    CandidateGroups,
    ToolTrace,
    TravelState,
    weather_by_date,
)
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.config import Settings
from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.draft import DraftDay, DraftItem, PlanDraft
from travel_agent.domain.models import DailyWeather
from travel_agent.domain.plan import TripPlan


def _weather_state() -> TravelState:
    forecast = forecast_chengdu()
    return cast(
        TravelState,
        {
            "weather": {daily.date.isoformat(): daily for daily in forecast.days},
            "city_location": forecast.location,
        },
    )


def _base_state() -> TravelState:
    return cast(
        TravelState,
        {
            "brief": chengdu_brief(),
            "candidates": chengdu_candidates(),
            "web_results": [],
            **_weather_state(),
        },
    )


async def test_parse_intent_node() -> None:
    brief = chengdu_brief()
    llm = FakeLLM(intent=IntentResult(intent="new_plan", brief=brief))
    state: TravelState = {"messages": [HumanMessage(content="帮我规划成都4天爱吃辣")]}
    result = await parse_intent(state, llm=llm)
    assert result["intent"] == "new_plan"
    assert isinstance(result["brief"], TravelBrief)
    assert result["brief"].destination == "成都"
    assert llm.calls[0][0] == "IntentResult"


async def test_clarify_node_asks_one_question() -> None:
    llm = FakeLLM(replies=["请问你打算哪天出发、几个人呢？"])
    state: TravelState = {
        "messages": [HumanMessage(content="想去成都")],
        "brief": TravelBrief(destination="成都"),
    }
    result = await clarify_brief(state, llm=llm)
    reply = cast(str, result["reply"])
    assert "哪天出发" in reply
    messages = cast(list[AIMessage], result["messages"])
    assert messages[0].content == reply


async def test_query_builders_use_brief() -> None:
    brief = chengdu_brief()
    assert any("武侯祠" in q.keyword for q in attraction_queries(brief))
    assert any("辣" in q.keyword for q in restaurant_queries(brief))


async def test_gather_candidates_groups_and_dedupes(registry: ToolRegistry) -> None:
    state: TravelState = {"brief": chengdu_brief()}
    result = await gather_candidates(state, ctx=registry)
    groups = cast(CandidateGroups, result["candidates"])
    assert {poi.poi_id for poi in groups["attractions"]} == {"a1", "a2", "a3", "a4", "a5"}
    assert {poi.poi_id for poi in groups["restaurants"]} == {"r1", "r2"}
    assert {poi.poi_id for poi in groups["hotels"]} == {"h1"}
    web_results = cast(list[object], result["web_results"])
    assert len(web_results) == 2
    traces = cast(list[ToolTrace], result["traces"])
    assert traces and all(trace.ok for trace in traces)


async def test_fetch_weather_success_and_degraded(fast_settings: Settings) -> None:
    state: TravelState = {"brief": chengdu_brief()}
    reg_ok = ToolRegistry(fast_settings, weather=FakeWeather())
    result = await fetch_weather(state, ctx=reg_ok)
    weather = cast(dict[str, DailyWeather], result["weather"])
    assert set(weather) == {"2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"}
    assert result["city_location"] is not None

    reg_bad = ToolRegistry(fast_settings, weather=FakeWeather(fail=True), maps=FakeMaps(fail=True))
    degraded = await fetch_weather(state, ctx=reg_bad)
    assert degraded["weather"] == {} and degraded["city_location"] is None


async def test_compose_node_hydrates_from_candidates(fast_settings: Settings) -> None:
    llm = FakeLLM(drafts=[valid_draft()])
    result = await compose_plan(_base_state(), llm=llm, settings=fast_settings)
    plan = cast(TripPlan, result["plan"])
    assert len(plan.days) == 4
    assert result["plan_version"] == 1 and result["loop_count"] == 0
    assert result["hydration_warnings"] == []
    # 防编造：事实条目全部带 poi_id 与来源
    fact_items = [item for _, item in plan.iter_items() if item.poi_id]
    assert fact_items and all(item.sources for item in fact_items)


async def test_validate_clean_then_revise_keeps_plan_id(fast_settings: Settings) -> None:
    base = _base_state()
    llm = FakeLLM(drafts=[valid_draft()])
    composed = await compose_plan(base, llm=llm, settings=fast_settings)
    plan = cast(TripPlan, composed["plan"])

    reg = ToolRegistry(fast_settings, maps=FakeMaps())
    checked_state = cast(TravelState, {**base, **composed})
    checked = await validate_plan(checked_state, ctx=reg, settings=fast_settings)
    assert checked["reflections"] == []
    assert checked["loop_count"] == 1

    revise_state: TravelState = cast(
        TravelState,
        {**base, "plan": plan, "plan_version": 1, "reflections": ["第 2 天空置"]},
    )
    llm2 = FakeLLM(drafts=[valid_draft()])
    revised = await revise_plan(revise_state, llm=llm2, settings=fast_settings)
    revised_plan = cast(TripPlan, revised["plan"])
    assert revised_plan.plan_id == plan.plan_id
    assert revised["plan_version"] == 2


async def test_validate_flags_empty_day(fast_settings: Settings) -> None:
    draft = valid_draft()
    bad = draft.model_copy(
        update={
            "days": [
                draft.days[0],
                DraftDay(date=date(2026, 10, 2), items=[]),
                draft.days[2],
                draft.days[3],
            ]
        }
    )
    llm = FakeLLM(drafts=[bad])
    base: TravelState = cast(
        TravelState,
        {
            "brief": chengdu_brief(),
            "candidates": chengdu_candidates(),
            "web_results": [],
            "weather": {},
            "city_location": None,
        },
    )
    composed = await compose_plan(base, llm=llm, settings=fast_settings)
    reg = ToolRegistry(fast_settings, maps=FakeMaps())
    checked = await validate_plan(
        cast(TravelState, {**base, **composed}), ctx=reg, settings=fast_settings
    )
    reflections = cast(list[str], checked["reflections"])
    assert any("没有任何安排" in text for text in reflections)


async def test_respond_plan_and_chat_modes(fast_settings: Settings) -> None:
    llm = FakeLLM(drafts=[valid_draft()], replies=["成都四日行程已生成"])
    composed = await compose_plan(_base_state(), llm=llm, settings=fast_settings)
    plan_state: TravelState = cast(
        TravelState, {**_base_state(), **composed, "loop_count": 1, "reflections": []}
    )
    plan_reply = await respond(plan_state, llm=llm, settings=fast_settings)
    assert plan_reply["reply"] == "成都四日行程已生成"

    # modify_plan 不再走 respond 的 chat 分支（阶段四改为 patch_plan → validate → respond）
    chat_llm = FakeLLM()
    chat_state: TravelState = {
        "messages": [HumanMessage(content="成都天气怎么样")],
        "intent": "ask_info",
        "reply_hint": "成都十月平均气温16-24度",
    }
    chat_reply = await respond(chat_state, llm=chat_llm, settings=fast_settings)
    assert chat_reply["reply"]
    # respond 用户模板包含用户问题
    assert "成都天气" in chat_llm.calls[0][2]


async def test_weather_by_date_restores_date_keys() -> None:
    restored = weather_by_date(_weather_state())
    assert restored[date(2026, 10, 1)].condition in {"晴", "多云"}


async def test_parse_and_clarify_fallback_without_human_message() -> None:
    llm = FakeLLM(intent=IntentResult(intent="chitchat", brief=TravelBrief()))
    result = await parse_intent(cast(TravelState, {"messages": []}), llm=llm)
    assert result["intent"] == "chitchat"
    assert llm.calls[0][2]  # user 模板仍被渲染

    # 非字符串 content 走拼接回退
    await parse_intent(
        cast(TravelState, {"messages": [HumanMessage(content=["多部分内容"])]}), llm=llm
    )

    clarify_state: TravelState = {
        "messages": [],
        "brief": TravelBrief(destination="成都"),
    }
    reply = await clarify_brief(clarify_state, llm=llm)
    assert reply["reply"]


async def test_respond_chat_without_human_message_and_plan_guard(
    fast_settings: Settings,
) -> None:
    llm = FakeLLM()
    state: TravelState = {
        "messages": [AIMessage(content="在的")],
        "intent": "chitchat",
    }
    reply = await respond(state, llm=llm, settings=fast_settings)
    assert reply["reply"]
    with pytest.raises(RuntimeError):
        _plan_prompt(cast(TravelState, {}), fast_settings)


async def test_search_weather_validate_revise_guards(
    fast_settings: Settings, registry: ToolRegistry
) -> None:
    undated: TravelState = {"brief": TravelBrief(destination="成都")}
    with pytest.raises(RuntimeError):
        await gather_candidates(undated, ctx=registry)
    with pytest.raises(RuntimeError):
        await fetch_weather(undated, ctx=registry)
    with pytest.raises(RuntimeError):
        await validate_plan(cast(TravelState, {"plan": None}), ctx=registry, settings=fast_settings)
    with pytest.raises(RuntimeError):
        await revise_plan(cast(TravelState, {"plan": None}), llm=FakeLLM(), settings=fast_settings)
    with pytest.raises(RuntimeError):
        await patch_plan(cast(TravelState, {"plan": None}), llm=FakeLLM(), settings=fast_settings)


def test_sort_results_ignores_non_list_values() -> None:
    groups, web = _sort_results(["tips", "poi:attractions"], [None, "oops"])
    assert groups == {"attractions": [], "restaurants": [], "hotels": []}
    assert web == []


async def test_day_pairs_skip_items_without_location(fast_settings: Settings) -> None:
    # 空候选目录 + 自由条目：水合后条目无坐标，通勤对应全部跳过
    free = PlanDraft(
        days=[
            DraftDay(
                date=date(2026, 10, 1),
                items=[
                    DraftItem(title="城市漫步", category="activity"),
                    DraftItem(title="自由休息", category="note"),
                ],
            )
        ]
    )
    llm = FakeLLM(drafts=[free])
    brief = chengdu_brief().model_copy(update={"end_date": date(2026, 10, 1)})
    free_state: TravelState = cast(
        TravelState,
        {
            "brief": brief,
            "candidates": {},
            "web_results": [],
            "weather": {},
            "city_location": None,
        },
    )
    composed = await compose_plan(free_state, llm=llm, settings=fast_settings)
    plan = cast(TripPlan, composed["plan"])
    assert _day_pairs(plan.days[0]) == []

    # 合规草稿第一天有两个带坐标条目：恰好一对
    llm2 = FakeLLM(drafts=[valid_draft()])
    composed2 = await compose_plan(_base_state(), llm=llm2, settings=fast_settings)
    plan2 = cast(TripPlan, composed2["plan"])
    pairs = _day_pairs(plan2.days[0])
    assert len(pairs) == 1 and pairs[0][0] == "d1-1" and pairs[0][1] == "d1-2"


def test_build_queries_with_and_without_destination() -> None:
    from travel_agent.agent.nodes.answer_info import build_queries

    with_city = build_queries("三天怎么玩", "成都")
    assert len(with_city) == 2
    assert all("成都" in q for q in with_city)

    no_city = build_queries("第一次出国去哪", "")
    assert len(no_city) == 1 and "第一次出国去哪" in no_city[0]


async def test_answer_info_gathers_grounded_hint(registry: ToolRegistry) -> None:
    from travel_agent.agent.nodes.answer_info import answer_info

    state: TravelState = cast(
        TravelState,
        {
            "messages": [HumanMessage(content="成都带小孩怎么玩")],
            "brief": chengdu_brief(),
        },
    )
    result = await answer_info(state, ctx=registry)
    hint = cast(str, result["reply_hint"])
    # FakeSearch 返回真实素材标题，hint 应含资料而非降级文案
    assert hint
    assert "未检索到" not in hint
    assert isinstance(result["web_results"], list) and result["web_results"]
    assert isinstance(result["traces"], list)


async def test_answer_info_degrades_to_hint_when_search_fails(
    fast_settings: Settings,
) -> None:
    from agent_fakes import FakeSearch

    from travel_agent.agent.nodes.answer_info import answer_info

    broken = ToolRegistry(fast_settings, search=FakeSearch(fail=True))
    state: TravelState = cast(TravelState, {"messages": [HumanMessage(content="成都天气")]})
    result = await answer_info(state, ctx=broken)
    assert "未检索到" in cast(str, result["reply_hint"])
