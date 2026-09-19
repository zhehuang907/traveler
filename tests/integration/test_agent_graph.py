"""LangGraph 端到端测试：路由分支、校验-修订循环、checkpointer 持久化。"""

from datetime import date

from agent_fakes import (
    FakeLLM,
    FakeMaps,
    FakeSearch,
    FakeWeather,
    chengdu_brief,
    chengdu_candidates,
    forecast_chengdu,
    valid_draft,
)
from langchain_core.messages import HumanMessage

from travel_agent.agent.checkpointer import open_checkpointer
from travel_agent.agent.contracts import IntentResult
from travel_agent.agent.graph import build_graph, route_after_intent, route_after_validate
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.config import Settings
from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.draft import DraftDay, DraftItem, PlanDraft
from travel_agent.domain.models import DailyWeather
from travel_agent.domain.plan import TripPlan


def _registry(settings: Settings) -> ToolRegistry:
    return ToolRegistry(settings, search=FakeSearch(), maps=FakeMaps(), weather=FakeWeather())


def _bad_draft() -> PlanDraft:
    """第 2 天空置：必然触发规则 reflection。"""
    draft = valid_draft()
    return draft.model_copy(
        update={
            "days": [
                draft.days[0],
                DraftDay(date=date(2026, 10, 2), items=[]),
                draft.days[2],
                draft.days[3],
            ]
        }
    )


# ---------- 路由纯函数 ----------


def test_route_after_intent_branches() -> None:
    ready = chengdu_brief()
    assert route_after_intent({"intent": "new_plan", "brief": ready}) == "search"
    partial = TravelBrief(destination="成都")
    assert route_after_intent({"intent": "new_plan", "brief": partial}) == "clarify_brief"
    # 必填齐备但规划前偏好未答复（攻略/特定项目/出行方式）→ 先聚合确认一次
    preferences_unanswered = ready.model_copy(update={"guide_ready": None, "transport": None})
    assert (
        route_after_intent({"intent": "new_plan", "brief": preferences_unanswered})
        == "clarify_brief"
    )
    assert route_after_intent({"intent": "chitchat"}) == "respond"
    assert route_after_intent({"intent": "ask_info"}) == "answer_info"
    assert route_after_intent({"intent": "modify_plan", "brief": ready}) == "patch_plan"


def test_route_after_validate_branches() -> None:
    assert route_after_validate({"reflections": [], "loop_count": 1}, 3) == "respond"
    assert route_after_validate({"reflections": ["x"], "loop_count": 1}, 3) == "revise_plan"
    assert route_after_validate({"reflections": ["x"], "loop_count": 3}, 3) == "respond"


# ---------- 端到端 ----------


async def test_graph_happy_path_chengdu_four_days(fast_settings: Settings) -> None:
    llm = FakeLLM(
        intent=IntentResult(intent="new_plan", brief=chengdu_brief()),
        drafts=[valid_draft()],
        replies=["这是你的成都四日行程"],
    )
    graph = build_graph(settings=fast_settings, llm=llm, ctx=_registry(fast_settings))
    result = await graph.ainvoke({"messages": [HumanMessage(content="成都4天预算5000爱吃辣")]})

    plan: TripPlan = result["plan"]
    assert len(plan.days) == 4
    assert result["reflections"] == []
    assert result["reply"] == "这是你的成都四日行程"
    # 天气经状态往返仍为领域类型
    assert isinstance(next(iter(result["weather"].values())), DailyWeather)
    # 防编造：事实条目都有 poi_id 且可在候选中找到、带来源
    candidate_ids = {poi.poi_id for group in result["candidates"].values() for poi in group}
    fact_items = [item for _, item in plan.iter_items() if item.poi_id]
    assert fact_items
    assert all(item.poi_id in candidate_ids and item.sources for item in fact_items)
    # 预算约束：总花费不超 brief 预算
    assert plan.total_cost_cny <= 5000
    # LLM 调用顺序：intent → compose(PlanDraft) → respond(文本)
    assert [call[0] for call in llm.calls] == ["IntentResult", "PlanDraft", "text"]


async def test_graph_revise_loop_converges(fast_settings: Settings) -> None:
    llm = FakeLLM(
        intent=IntentResult(intent="new_plan", brief=chengdu_brief()),
        drafts=[_bad_draft(), valid_draft()],
        replies=["修订后的行程"],
    )
    graph = build_graph(settings=fast_settings, llm=llm, ctx=_registry(fast_settings))
    result = await graph.ainvoke({"messages": [HumanMessage(content="成都4天")]})

    assert result["plan_version"] == 2
    assert result["loop_count"] == 2
    assert result["reflections"] == []
    assert [call[0] for call in llm.calls] == [
        "IntentResult",
        "PlanDraft",
        "PlanDraft",
        "text",
    ]
    # 修订输入中带程序化反馈原文
    revise_user = llm.calls[2][2]
    assert "没有任何安排" in revise_user


async def test_graph_forced_delivery_when_loops_exhausted(settings: Settings) -> None:
    fast = settings.model_copy(update={"poi_min_interval_s": 0.0, "max_revise_loops": 1})
    llm = FakeLLM(
        intent=IntentResult(intent="new_plan", brief=chengdu_brief()),
        drafts=[_bad_draft()],
        replies=["仍有问题但先交付"],
    )
    graph = build_graph(settings=fast, llm=llm, ctx=_registry(fast))
    result = await graph.ainvoke({"messages": [HumanMessage(content="成都4天")]})

    assert result["loop_count"] == 1
    assert result["reflections"]  # 残留问题未消除
    assert result["reply"] == "仍有问题但先交付"
    # 强制交付时残留问题回灌给 respond 提示
    respond_user = llm.calls[2][2]
    assert "仍存在" in respond_user


async def test_graph_clarify_branch(fast_settings: Settings) -> None:
    llm = FakeLLM(
        intent=IntentResult(intent="new_plan", brief=TravelBrief(destination="成都")),
        replies=["请问你打算哪天出发、几个人？"],
    )
    graph = build_graph(settings=fast_settings, llm=llm, ctx=_registry(fast_settings))
    result = await graph.ainvoke({"messages": [HumanMessage(content="想去成都玩")]})
    assert "哪天出发" in result["reply"]
    assert "plan" not in result


async def test_graph_chitchat_branch(fast_settings: Settings) -> None:
    llm = FakeLLM(
        intent=IntentResult(intent="chitchat", brief=TravelBrief()),
        replies=["你好呀"],
    )
    graph = build_graph(settings=fast_settings, llm=llm, ctx=_registry(fast_settings))
    result = await graph.ainvoke({"messages": [HumanMessage(content="你好")]})
    assert result["reply"] == "你好呀"


async def test_graph_ask_info_grounded_answer(fast_settings: Settings) -> None:
    """ask_info → answer_info（检索）→ respond，回复基于检索资料。"""
    llm = FakeLLM(
        intent=IntentResult(intent="ask_info", brief=chengdu_brief()),
        replies=["### 行程建议\n- 第一天去熊猫基地"],
    )
    graph = build_graph(settings=fast_settings, llm=llm, ctx=_registry(fast_settings))
    result = await graph.ainvoke({"messages": [HumanMessage(content="成都带小孩怎么玩")]})
    assert result["reply"]
    assert result.get("web_results")
    # respond 的 user 模板应包含检索资料（应答素材）
    assert "应答素材" in llm.calls[1][2]
    # 调用顺序：意图 → 文本回答（中间 answer_info 不调 LLM）
    assert [call[0] for call in llm.calls] == ["IntentResult", "text"]


async def test_graph_modify_plan_routes_to_patch(fast_settings: Settings) -> None:
    """modify_plan → patch_plan → validate_plan → respond，plan_id 保持稳定。"""
    from travel_agent.agent.planning import build_catalog
    from travel_agent.domain.plan_builder import hydrate_plan

    brief = chengdu_brief()
    catalog = build_catalog(chengdu_candidates())
    weather_map = {d.date: d for d in forecast_chengdu().days}
    original = hydrate_plan(valid_draft(), brief, catalog, weather_map, None).plan

    # 修改版草稿：把第 2 天换成熊猫基地（ref=4，原本已是 4，改成不同时段验证 diff）
    modified = valid_draft()
    modified = modified.model_copy(
        update={
            "days": [
                modified.days[0],
                DraftDay(
                    date=date(2026, 10, 2),
                    items=[
                        DraftItem(
                            title="候选5",
                            category="attraction",
                            candidate_ref=5,
                            start_clock="09:00",
                            end_clock="11:00",
                            cost_cny=50,
                        ),
                    ],
                ),
                modified.days[2],
                modified.days[3],
            ]
        }
    )

    llm = FakeLLM(
        intent=IntentResult(
            intent="modify_plan",
            brief=brief,
            target_scope=["第2天"],
        ),
        drafts=[modified],
        replies=["已将第二天换成杜甫草堂"],
    )
    graph = build_graph(settings=fast_settings, llm=llm, ctx=_registry(fast_settings))
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="第二天换成杜甫草堂")],
            "brief": brief,
            "plan": original,
            "candidates": chengdu_candidates(),
            "weather": {day.isoformat(): daily for day, daily in weather_map.items()},
        }
    )

    assert result["plan_version"] == 2
    diff = result["plan_diff"]
    assert diff is not None
    assert not diff.is_empty
    # plan_id 保持稳定
    assert result["plan"].plan_id == original.plan_id
    # LLM 调用顺序：intent → patch(PlanDraft) → respond(文本)
    assert [call[0] for call in llm.calls] == ["IntentResult", "PlanDraft", "text"]


async def test_checkpointer_persists_and_restores_pydantic_state(
    fast_settings: Settings,
) -> None:
    llm = FakeLLM(
        intent=IntentResult(intent="new_plan", brief=chengdu_brief()),
        drafts=[valid_draft()],
        replies=["完成"],
    )
    config = {"configurable": {"thread_id": "thread-chengdu-1"}}
    async with open_checkpointer(fast_settings) as checkpointer:
        graph = build_graph(
            settings=fast_settings,
            llm=llm,
            ctx=_registry(fast_settings),
            checkpointer=checkpointer,
        )
        await graph.ainvoke({"messages": [HumanMessage(content="成都4天")]}, config=config)
        snapshot = await graph.aget_state(config)

    assert snapshot.values["plan"].plan_id
    assert len(snapshot.values["plan"].days) == 4
    assert isinstance(snapshot.values["brief"], TravelBrief)
    weather = snapshot.values["weather"]
    assert isinstance(next(iter(weather.values())), DailyWeather)
