"""搜索 Provider 与 SearchService 集成测试（全部 respx mock，禁止真实网络）。"""

import json
import sys

import httpx
import pytest
import respx
from pydantic import SecretStr

from travel_agent.config import Settings
from travel_agent.services.errors import AllProvidersFailed, ProviderError
from travel_agent.services.search.bocha import BochaSearchProvider
from travel_agent.services.search.duckduckgo import DuckDuckGoSearchProvider
from travel_agent.services.search.service import SearchService
from travel_agent.services.search.tavily import TavilySearchProvider

_TAVILY_URL = "https://api.tavily.com/search"
_BOCHA_URL = "https://api.bochaai.com/v1/web-search"


def _fast_retry(settings: Settings, **updates: object) -> Settings:
    updates.setdefault("external_retry_base_delay", 0.001)
    return settings.model_copy(update=updates)


# ---------------- Tavily ----------------


@respx.mock
async def test_tavily_parses_results(settings: Settings) -> None:
    s = _fast_retry(settings, tavily_api_key=SecretStr("tv-secret"))
    route = respx.post(_TAVILY_URL).respond(
        200,
        json={
            "results": [
                {
                    "title": "北京旅游攻略",
                    "url": "https://example.com/a",
                    "content": "摘要A",
                    "published_date": "2026-09-01",
                },
                {"title": "无有效链接", "url": "", "content": "跳过"},
            ]
        },
    )
    provider = TavilySearchProvider(s)
    assert provider.configured is True
    results = await provider.search("北京 旅游", 5, "week")
    assert len(results) == 1
    assert results[0].title == "北京旅游攻略"
    assert results[0].provider == "tavily"
    assert results[0].published_at is not None
    assert results[0].published_at.isoformat() == "2026-09-01"

    request = route.calls.last.request
    payload = json.loads(request.content)
    assert payload["query"] == "北京 旅游"
    assert payload["max_results"] == 5
    assert payload["search_depth"] == "basic"
    assert payload["time_range"] == "week"
    assert request.headers["Authorization"] == "Bearer tv-secret"


@respx.mock
async def test_tavily_omits_time_range_when_none(settings: Settings) -> None:
    s = _fast_retry(settings, tavily_api_key=SecretStr("tv"))
    route = respx.post(_TAVILY_URL).respond(200, json={"results": []})
    await TavilySearchProvider(s).search("q", 3, None)
    assert "time_range" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_tavily_500_fails_fast_at_provider_level(settings: Settings) -> None:
    # Provider 自身不重试；重试统一在 run_chain 层
    s = _fast_retry(settings, tavily_api_key=SecretStr("tv"))
    route = respx.post(_TAVILY_URL).respond(500)
    with pytest.raises(httpx.HTTPStatusError):
        await TavilySearchProvider(s).search("q", 3, None)
    assert route.call_count == 1


@respx.mock
async def test_service_retries_tavily_500_then_fails(settings: Settings) -> None:
    s = _fast_retry(
        settings,
        search_provider_chain=["tavily"],
        tavily_api_key=SecretStr("tv"),
    )
    route = respx.post(_TAVILY_URL)
    route.side_effect = [httpx.Response(500) for _ in range(3)]
    with pytest.raises(AllProvidersFailed):
        await SearchService(s).search("q")
    assert route.call_count == 3


@respx.mock
async def test_tavily_400_fails_fast(settings: Settings) -> None:
    s = _fast_retry(settings, tavily_api_key=SecretStr("tv"))
    route = respx.post(_TAVILY_URL).respond(400)
    with pytest.raises(httpx.HTTPStatusError):
        await TavilySearchProvider(s).search("q", 3, None)
    assert route.call_count == 1


# ---------------- Bocha ----------------


@respx.mock
async def test_bocha_parses_results_and_freshness(settings: Settings) -> None:
    s = _fast_retry(settings, bocha_api_key=SecretStr("bc-secret"))
    route = respx.post(_BOCHA_URL).respond(
        200,
        json={
            "data": {
                "webPages": {
                    "value": [
                        {
                            "name": "博查结果",
                            "url": "https://b.example/x",
                            "snippet": "摘要",
                            "datePublished": "2026-08-01T10:00:00Z",
                        }
                    ]
                }
            }
        },
    )
    provider = BochaSearchProvider(s)
    assert provider.configured is True
    results = await provider.search("上海", 8, "month")
    assert len(results) == 1
    assert results[0].provider == "bocha"
    payload = json.loads(route.calls.last.request.content)
    assert payload == {"query": "上海", "count": 8, "freshness": "oneMonth"}
    assert route.calls.last.request.headers["Authorization"] == "Bearer bc-secret"


@respx.mock
@pytest.mark.parametrize(
    "body",
    [{}, {"data": None}, {"data": {"webPages": None}}, {"data": {}}],
)
async def test_bocha_empty_envelope_returns_empty(
    settings: Settings, body: dict[str, object]
) -> None:
    s = _fast_retry(settings, bocha_api_key=SecretStr("bc"))
    respx.post(_BOCHA_URL).respond(200, json=body)
    assert await BochaSearchProvider(s).search("q", 3, None) == []


# ---------------- DuckDuckGo（注入假客户端，不触网） ----------------


class FakeDDGS:
    """记录入参的 DDGS 测试替身。"""

    def __init__(self, items: list[dict[str, str]]) -> None:
        self.items = items
        self.calls: list[dict[str, object]] = []

    def text(
        self,
        keywords: str,
        region: str | None = None,
        safesearch: str = "moderate",
        timelimit: str | None = None,
        backend: str = "api",
        max_results: int | None = None,
    ) -> list[dict[str, str]]:
        self.calls.append(
            {
                "keywords": keywords,
                "region": region,
                "safesearch": safesearch,
                "timelimit": timelimit,
                "max_results": max_results,
            }
        )
        return self.items


async def test_ddg_maps_arguments_and_filters_bad_rows(settings: Settings) -> None:
    fake = FakeDDGS(
        [
            {"title": "DDG结果", "href": "https://d.example/x", "body": "正文"},
            {"title": "缺链接", "href": "", "body": ""},
        ]
    )
    provider = DuckDuckGoSearchProvider(settings, ddgs_factory=lambda: fake)
    assert provider.configured is True
    results = await provider.search("杭州", 4, "week")
    assert len(results) == 1
    assert results[0].provider == "duckduckgo"
    assert fake.calls == [
        {
            "keywords": "杭州",
            "region": "wt-wt",
            "safesearch": "moderate",
            "timelimit": "w",
            "max_results": 4,
        }
    ]


async def test_ddg_none_time_range(settings: Settings) -> None:
    fake = FakeDDGS([])
    provider = DuckDuckGoSearchProvider(settings, ddgs_factory=lambda: fake)
    await provider.search("q", 2, None)
    assert fake.calls[0]["timelimit"] is None


async def test_ddg_missing_dependency_raises(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "duckduckgo_search", None)
    provider = DuckDuckGoSearchProvider(settings)
    with pytest.raises(ProviderError, match="依赖不可用"):
        await provider.search("q", 2, None)


# ---------------- SearchService 降级链与缓存 ----------------


@respx.mock
async def test_service_all_unconfigured_fails(settings: Settings) -> None:
    s = _fast_retry(settings, search_provider_chain=["tavily", "bocha"])
    service = SearchService(s)
    with pytest.raises(AllProvidersFailed, match="web_search"):
        await service.search("无可用 Key")


@respx.mock
async def test_service_falls_back_to_bocha_and_caches(settings: Settings) -> None:
    s = _fast_retry(
        settings,
        search_provider_chain=["tavily", "bocha"],
        bocha_api_key=SecretStr("bc"),
    )
    route = respx.post(_BOCHA_URL).respond(
        200,
        json={
            "data": {
                "webPages": {
                    "value": [
                        {"name": "兜底", "url": "https://b.example/1", "snippet": "s"},
                    ]
                }
            }
        },
    )
    service = SearchService(s)
    first = await service.search("成都", max_results=3)
    second = await service.search("成都", max_results=3)
    assert [r.provider for r in first] == ["bocha"]
    assert len(second) == 1
    assert route.call_count == 1  # 第二次命中缓存，不再发请求


@respx.mock
async def test_service_unknown_provider_name_ignored(settings: Settings) -> None:
    s = _fast_retry(
        settings,
        search_provider_chain=["nope", "bocha"],
        bocha_api_key=SecretStr("bc"),
    )
    respx.post(_BOCHA_URL).respond(200, json={"data": {"webPages": {"value": []}}})
    service = SearchService(s)
    assert await service.search("q") == []


@pytest.mark.parametrize("query", ["", "   ", "字" * 201])
async def test_service_validates_query(settings: Settings, query: str) -> None:
    service = SearchService(settings)
    with pytest.raises(ValueError):
        await service.search(query, max_results=5)


@respx.mock
async def test_service_caps_max_results(settings: Settings) -> None:
    s = _fast_retry(
        settings,
        search_provider_chain=["bocha"],
        bocha_api_key=SecretStr("bc"),
    )
    route = respx.post(_BOCHA_URL).respond(200, json={"data": {"webPages": {"value": []}}})
    await SearchService(s).search("q", max_results=99)
    assert json.loads(route.calls.last.request.content)["count"] == 10
