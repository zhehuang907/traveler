"""TTLCache 与 stable_key 测试（真实 diskcache，目录隔离到 tmp_path）。"""

import pytest

from travel_agent.config import Settings
from travel_agent.domain.models import GeoPoint
from travel_agent.services.cache import TTLCache, stable_key


def test_stable_key_order_independent_and_namespaced() -> None:
    first = stable_key("web_search", "tavily", q="北京", n=5)
    second = stable_key("web_search", "tavily", n=5, q="北京")
    assert first == second
    assert first.startswith("web_search:")
    # namespace / provider / 参数任一不同都应产生不同键
    assert stable_key("poi", "tavily", q="北京", n=5) != first
    assert stable_key("web_search", "bocha", q="北京", n=5) != first
    assert stable_key("web_search", "tavily", q="上海", n=5) != first


async def test_get_or_set_miss_then_hit(settings: Settings) -> None:
    cache = TTLCache(settings)
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        return "value"

    value, first_hit = await cache.get_or_set("k", 60, factory)
    assert (value, first_hit) == ("value", False)
    value, second_hit = await cache.get_or_set("k", 60, factory)
    assert (value, second_hit) == ("value", True)
    assert calls == 1
    cache.close()


async def test_cache_preserves_pydantic_type(settings: Settings) -> None:
    cache = TTLCache(settings)
    point = GeoPoint(lat=39.9, lng=116.4)

    async def factory() -> GeoPoint:
        return point

    value, hit = await cache.get_or_set("geo", 60, factory)
    assert hit is False
    assert value == point
    cached, cached_hit = await cache.get("geo")
    assert cached_hit is True
    assert isinstance(cached, GeoPoint)
    assert cached == point
    cache.close()


async def test_get_missing_returns_miss(settings: Settings) -> None:
    cache = TTLCache(settings)
    value, hit = await cache.get("absent")
    assert value is None
    assert hit is False
    cache.close()


async def test_cache_read_failure_degrades_to_miss(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = TTLCache(settings)

    def broken_client() -> object:
        raise RuntimeError("disk unavailable")

    monkeypatch.setattr(cache, "_get_client", broken_client)
    value, hit = await cache.get("k")
    assert value is None
    assert hit is False

    # 写故障也被吞掉：factory 照常执行，返回值不受影响
    calls = 0

    async def factory() -> str:
        nonlocal calls
        calls += 1
        return "direct"

    result, result_hit = await cache.get_or_set("k2", 60, factory)
    assert (result, result_hit, calls) == ("direct", False, 1)
    cache.close()


async def test_clear_evicts_entries(settings: Settings) -> None:
    cache = TTLCache(settings)

    async def factory() -> str:
        return "x"

    await cache.get_or_set("k", 60, factory)
    cache.clear()
    _value, hit = await cache.get("k")
    assert hit is False
    cache.close()
