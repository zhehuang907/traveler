"""工具注册表与工具函数测试（信号量/trace/降级/节流；假服务不触网）。"""

import asyncio
from datetime import date

import pytest
from agent_fakes import FakeMaps, FakeSearch, FakeWeather

from travel_agent.agent.tools import (
    IntervalLimiter,
    geocode,
    get_route,
    get_weather,
    search_hotel,
    search_poi,
    search_tips,
    search_web,
)
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.config import Settings
from travel_agent.domain.models import GeoPoint, RouteInfo


async def test_registry_records_success_trace(fast_settings: Settings) -> None:
    reg = ToolRegistry(fast_settings, search=FakeSearch(), maps=FakeMaps(), weather=FakeWeather())
    results = await search_web(reg, "成都 攻略")
    assert results
    traces = reg.trace_snapshot()
    assert traces and traces[0].ok and "条结果" in traces[0].detail
    # 快照深拷贝：外部修改不污染内部状态
    traces[0] = traces[0].model_copy(update={"ok": False})
    assert reg.trace_snapshot()[0].ok


async def test_registry_swallows_exception_as_none(fast_settings: Settings) -> None:
    reg = ToolRegistry(
        fast_settings,
        search=FakeSearch(fail=True),
        maps=FakeMaps(fail=True),
        weather=FakeWeather(fail=True),
    )
    assert await search_web(reg, "x") == []
    assert await search_poi(reg, "成都", "景点") == []
    assert await geocode(reg, "成都") is None
    assert await get_weather(reg, "成都", date(2026, 10, 1), date(2026, 10, 2)) is None
    assert all(trace.degraded for trace in reg.trace_snapshot())


async def test_route_and_hotel_tools(fast_settings: Settings) -> None:
    reg = ToolRegistry(fast_settings, maps=FakeMaps())
    route = await get_route(reg, GeoPoint(lat=30, lng=104), GeoPoint(lat=31, lng=105))
    assert isinstance(route, RouteInfo) and route.duration_s == 600

    hotels = await search_hotel(reg, "成都", date(2026, 10, 1), date(2026, 10, 4), budget_cny=5000)
    assert hotels and hotels[0].poi_id == "h1"
    with pytest.raises(ValueError):
        await search_hotel(reg, "成都", date(2026, 10, 5), date(2026, 10, 1))


async def test_search_tips_query(fast_settings: Settings) -> None:
    search = FakeSearch()
    reg = ToolRegistry(fast_settings, search=search)
    results = await search_tips(reg, "成都")
    assert results
    assert any("避坑" in query for query in search.queries)


async def test_registry_summarizes_empty_result(fast_settings: Settings) -> None:
    reg = ToolRegistry(fast_settings, search=FakeSearch(), maps=FakeMaps(), weather=FakeWeather())

    async def return_none() -> object:
        """成功但无数据的工具调用。"""
        return None

    assert await reg.run("noop", return_none) is None
    assert reg.trace_snapshot()[0].detail == "空结果"


async def test_interval_limiter_zero_and_positive() -> None:
    limiter = IntervalLimiter(0.0)
    async with limiter.throttle():
        pass  # 立即放行，不产生 sleep

    paced = IntervalLimiter(0.05)

    async def one_pass() -> float:
        loop = asyncio.get_running_loop()
        async with paced.throttle():
            return loop.time()

    first, second = await asyncio.gather(one_pass(), one_pass())
    # 两次放行至少相差一个最小间隔（考虑调度误差给小余量）
    assert abs(second - first) >= 0.045
