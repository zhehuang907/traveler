"""agent/planning.py 文本格式化测试。"""

from datetime import date

from travel_agent.agent.planning import (
    build_catalog,
    format_catalog,
    format_plan_with_refs,
    format_weather,
    format_web,
)
from travel_agent.domain.models import DailyWeather, GeoPoint, Poi, SearchResult
from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan


def _poi(poi_id: str = "a1", **overrides: object) -> Poi:
    base: dict[str, object] = {
        "poi_id": poi_id,
        "name": "无名地点",
        "location": GeoPoint(lat=30.0, lng=104.0),
        "provider": "amap",
    }
    base.update(overrides)
    return Poi.model_validate(base)


def test_format_catalog_optional_fields() -> None:
    bare = format_catalog(build_catalog({"attractions": [_poi()], "restaurants": [], "hotels": []}))
    assert "#1" in bare and "评分" not in bare and "营业" not in bare and "地址" not in bare

    full = format_catalog(
        build_catalog(
            {
                "attractions": [
                    _poi(
                        "a2",
                        name="武侯祠",
                        rating=4.5,
                        business_hours="09:00-18:00",
                        cost_hint="50元",
                        address="武侯祠大街",
                    )
                ],
                "restaurants": [],
                "hotels": [],
            }
        )
    )
    assert "评分4.5" in full and "09:00-18:00" in full and "50元" in full and "武侯祠大街" in full


def test_format_catalog_empty_hint() -> None:
    text = format_catalog(build_catalog({}))
    assert "无候选" in text


def test_format_weather_variants_and_empty() -> None:
    weather = {
        "2026-10-01": DailyWeather(
            date=date(2026, 10, 1),
            temp_min=10,
            temp_max=20,
            condition="晴",
            precip_prob=None,
            climate=True,
        )
    }
    text = format_weather(weather)
    assert "历史气候参考" in text and "降水未知" in text
    assert "天气不可用" in format_weather({})


def test_format_web_filters_empty_snippet() -> None:
    results = [
        SearchResult(title="有摘要", url="https://example.com/1", snippet="内容", provider="x"),
        SearchResult(title="无摘要", url="https://example.com/2", provider="x"),
    ]
    text = format_web(results)
    assert "有摘要" in text and "无摘要" not in text
    assert format_web([]) == ""


def test_format_plan_with_refs_known_and_free() -> None:
    plan = TripPlan(
        plan_id="p",
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 1),
        travelers=2,
        budget_cny=None,
        days=[
            PlanDay(
                day_index=1,
                date=date(2026, 10, 1),
                items=[
                    PlanItem(
                        item_id="d1-1",
                        poi_id="a1",
                        title="武侯祠",
                        category="attraction",
                        start_time="09:00",
                        end_time="11:00",
                    ),
                    PlanItem(item_id="d1-2", title="自由活动", category="note"),
                ],
            )
        ],
    )
    catalog = build_catalog({"attractions": [_poi()], "restaurants": [], "hotels": []})
    text = format_plan_with_refs(plan, catalog)
    assert "[候选#1]" in text and "[note]" in text and "09:00-11:00" in text and "时间待定" in text
