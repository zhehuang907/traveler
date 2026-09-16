"""Agent 集成测试替身与「成都 4 天」固定素材（全程禁真实网络）。"""

from datetime import date, timedelta
from typing import Any

from travel_agent.agent.contracts import IntentResult
from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.draft import DraftDay, DraftItem, PlanDraft
from travel_agent.domain.models import (
    DailyWeather,
    GeoPoint,
    Poi,
    RouteInfo,
    SearchResult,
    TravelMode,
    WeatherForecast,
)
from travel_agent.services.llm import StructuredLLM, TokenUsage
from travel_agent.services.search.base import SearchTimeRange

CHENGDU_START = date(2026, 10, 1)
CHENGDU_END = date(2026, 10, 4)
_CENTER = GeoPoint(lat=30.66, lng=104.07)


# ---------- LLM 替身 ----------


class FakeLLM(StructuredLLM):
    """按 schema 返回预置结果；记录全部调用供断言。"""

    def __init__(
        self,
        *,
        intent: IntentResult | None = None,
        drafts: list[PlanDraft] | None = None,
        replies: list[str] | None = None,
        default_reply: str = "这是为你生成的成都行程回复。",
    ) -> None:
        self._intent = intent or IntentResult(intent="chitchat", brief=TravelBrief())
        self._drafts = list(drafts or [])
        self._replies = list(replies or [])
        self.default_reply = default_reply
        self.usage = TokenUsage()
        # (schema名或"text", system, user)
        self.calls: list[tuple[str, str, str]] = []

    async def aparse(self, schema: type[Any], *, system: str, user: str) -> Any:
        self.calls.append((schema.__name__, system, user))
        if schema is IntentResult:
            return self._intent
        if not self._drafts:
            raise AssertionError("FakeLLM 未预置更多 PlanDraft")
        draft = self._drafts.pop(0)
        assert isinstance(draft, schema)
        return draft

    async def acomplete(self, *, system: str, user: str) -> str:
        self.calls.append(("text", system, user))
        if self._replies:
            return self._replies.pop(0)
        return self.default_reply

    def usage_snapshot(self) -> TokenUsage:
        return self.usage.model_copy()


# ---------- 外部服务替身 ----------


class FakeSearch:
    def __init__(self, results: list[SearchResult] | None = None, *, fail: bool = False) -> None:
        self.results = results if results is not None else web_results()
        self.fail = fail
        self.queries: list[str] = []

    async def search(
        self,
        query: str,
        max_results: int = 5,
        time_range: SearchTimeRange | None = "year",
    ) -> list[SearchResult]:
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("search provider down")
        return self.results[:max_results]


class FakeMaps:
    def __init__(self, *, fail: bool = False, route_seconds: int = 600) -> None:
        self.fail = fail
        self.route_seconds = route_seconds
        self.route_calls = 0

    async def search_poi(
        self, city: str, keywords: str, category: str | None = None, limit: int = 10
    ) -> list[Poi]:
        if self.fail:
            raise RuntimeError("maps provider down")
        if category == "100000" or "酒店" in keywords:
            return list(HOTELS)
        if "美食" in keywords or "餐厅" in keywords:
            return list(RESTAURANTS)
        return list(ATTRACTIONS)

    async def geocode(self, address: str) -> GeoPoint:
        if self.fail:
            raise RuntimeError("geocode down")
        return _CENTER

    async def route(
        self, origin: GeoPoint, destination: GeoPoint, mode: TravelMode = "driving"
    ) -> RouteInfo:
        self.route_calls += 1
        if self.fail:
            raise RuntimeError("route down")
        return RouteInfo(
            origin=origin,
            destination=destination,
            mode=mode,
            distance_m=3000,
            duration_s=self.route_seconds,
            provider="fake",
        )


class FakeWeather:
    def __init__(self, forecast: WeatherForecast | None = None, *, fail: bool = False) -> None:
        self.forecast = forecast if forecast is not None else forecast_chengdu()
        self.fail = fail

    async def get_weather(self, city: str, start: date, end: date) -> WeatherForecast:
        if self.fail:
            raise RuntimeError("weather provider down")
        return self.forecast


# ---------- 成都固定素材 ----------


def _poi(poi_id: str, name: str, *, cost: str, hours: str = "08:30-18:00") -> Poi:
    tail = int(poi_id[-1])
    return Poi(
        poi_id=poi_id,
        name=name,
        address=f"成都市{name}街1号",
        location=GeoPoint(lat=30.6 + tail * 0.01, lng=104.0 + tail * 0.01),
        rating=4.5,
        business_hours=hours,
        cost_hint=cost,
        sources=[f"https://example.amap.com/{poi_id}"],
        provider="amap",
    )


ATTRACTIONS = [
    _poi("a1", "武侯祠", cost="门票50元"),
    _poi("a2", "锦里古街", cost="免费"),
    _poi("a3", "宽窄巷子", cost="免费"),
    _poi("a4", "成都大熊猫繁育研究基地", cost="门票55元"),
    _poi("a5", "杜甫草堂", cost="门票50元"),
]
RESTAURANTS = [
    _poi("r1", "蜀大侠火锅", cost="人均100元", hours="11:00-22:00"),
    _poi("r2", "陈麻婆豆腐", cost="人均60元", hours="10:30-21:00"),
]
HOTELS = [_poi("h1", "春熙路商务酒店", cost="300元/晚", hours="24小时")]


def web_results() -> list[SearchResult]:
    return [
        SearchResult(
            title="成都旅游攻略",
            url="https://example.com/chengdu-guide",
            snippet="武侯祠与锦里相邻可同日游览，火锅建议提前排号。",
            provider="tavily",
        ),
        SearchResult(
            title="成都避坑提示",
            url="https://example.com/chengdu-tips",
            snippet="景区门口的拉客茶馆不建议消费。",
            provider="bocha",
        ),
    ]


def forecast_chengdu() -> WeatherForecast:
    days = [
        DailyWeather(
            date=CHENGDU_START + timedelta(days=i),
            temp_min=16 + i,
            temp_max=24 + i,
            condition=("多云" if i % 2 else "晴"),
            precip_prob=20 + i * 5,
        )
        for i in range(4)
    ]
    return WeatherForecast(city="成都", location=_CENTER, days=days, provider="fake")


def chengdu_brief() -> TravelBrief:
    return TravelBrief(
        destination="成都",
        start_date=CHENGDU_START,
        end_date=CHENGDU_END,
        travelers=2,
        budget_cny=5000,
        pace="moderate",
        preferences=["熊猫"],
        dietary=["辣"],
        must_visit=["武侯祠"],
    )


def chengdu_candidates() -> dict[str, list[Poi]]:
    return {
        "attractions": list(ATTRACTIONS),
        "restaurants": list(RESTAURANTS),
        "hotels": list(HOTELS),
    }


# ---------- 草稿工厂（候选编号：1-5 景点，6-7 餐饮，8 酒店） ----------


def valid_draft() -> PlanDraft:
    """4 天全部齐备、时段在营业时间内、通勤空档充裕、预算内的合规草稿。"""
    specs: list[list[tuple[int, str, str, str]]] = [
        [(1, "09:00", "11:00", "attraction"), (2, "11:30", "13:00", "attraction")],
        [(4, "08:30", "12:00", "attraction"), (6, "12:30", "13:30", "restaurant")],
        [(3, "09:30", "11:30", "attraction"), (7, "12:00", "13:00", "restaurant")],
        [(5, "09:00", "11:00", "attraction"), (8, "00:00", "23:59", "hotel")],
    ]
    days = []
    for i, items in enumerate(specs):
        days.append(
            DraftDay(
                date=CHENGDU_START + timedelta(days=i),
                items=[
                    DraftItem(
                        title=f"候选{ref}",
                        category=category,
                        candidate_ref=ref,
                        start_clock=start,
                        end_clock=end,
                        cost_cny=80,
                    )
                    for ref, start, end, category in items
                ],
            )
        )
    return PlanDraft(days=days, summary="成都四日游", tips=["带好肠胃药"])
