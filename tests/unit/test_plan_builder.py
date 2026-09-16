"""候选目录与草稿水合测试（防编造机制的核心保障）。"""

from datetime import date, timedelta

from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.draft import DraftDay, DraftItem, PlanDraft
from travel_agent.domain.models import DailyWeather, GeoPoint, Poi
from travel_agent.domain.plan_builder import CandidateCatalog, hydrate_plan


def _poi(poi_id: str, name: str, *, hours: str = "") -> Poi:
    return Poi(
        poi_id=poi_id,
        name=name,
        location=GeoPoint(lat=30.0, lng=104.0),
        business_hours=hours or None,
        sources=["https://example.com/a", f"https://example.com/{poi_id}"],
        provider="amap",
    )


def _brief() -> TravelBrief:
    return TravelBrief(
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 2),
        travelers=2,
        budget_cny=5000,
        pace="moderate",
    )


def _catalog() -> CandidateCatalog:
    return CandidateCatalog.from_groups(
        [_poi("a1", "武侯祠", hours="09:00-18:00"), _poi("a2", "锦里")],
        [_poi("r1", "蜀大侠火锅")],
        [_poi("h1", "春熙路酒店")],
    )


def test_catalog_numbering_groups_in_order() -> None:
    catalog = _catalog()
    refs = catalog.by_ref()
    assert [entry.poi.name for _, entry in sorted(refs.items())] == [
        "武侯祠",
        "锦里",
        "蜀大侠火锅",
        "春熙路酒店",
    ]
    assert catalog.business_hours() == {"a1": "09:00-18:00"}


def test_fact_item_without_valid_ref_is_dropped() -> None:
    draft = PlanDraft(
        days=[
            DraftDay(
                date=date(2026, 10, 1),
                items=[
                    DraftItem(title="编造的神秘景点", category="attraction", candidate_ref=99),
                    DraftItem(title="武侯祠", category="attraction", candidate_ref=1),
                ],
            ),
            DraftDay(date=date(2026, 10, 2), items=[]),
        ]
    )
    result = hydrate_plan(draft, _brief(), _catalog(), {}, None)
    day1 = result.plan.days[0]
    assert len(day1.items) == 1
    assert day1.items[0].title == "武侯祠"
    assert day1.items[0].sources
    assert result.dropped == ("编造的神秘景点",)
    assert any("无候选来源" in warning for warning in result.warnings)


def test_free_item_kept_without_coordinates_or_sources() -> None:
    draft = PlanDraft(
        days=[
            DraftDay(
                date=date(2026, 10, 1),
                items=[
                    DraftItem(title="城市漫步", category="activity"),
                    DraftItem(title="地铁前往春熙路", category="transport"),
                    DraftItem(title="自由休息", category="note"),
                ],
            ),
            DraftDay(date=date(2026, 10, 2), items=[]),
        ]
    )
    result = hydrate_plan(draft, _brief(), _catalog(), {}, None)
    items = result.plan.days[0].items
    assert [item.title for item in items] == ["城市漫步", "地铁前往春熙路", "自由休息"]
    assert all(item.location is None and item.sources == [] for item in items)
    assert result.dropped == ()


def test_rainy_day_marks_indoor_items() -> None:
    weather = {
        date(2026, 10, 1): DailyWeather(
            date=date(2026, 10, 1), temp_min=10, temp_max=18, condition="小雨", precip_prob=90
        )
    }
    draft = PlanDraft(
        days=[
            DraftDay(
                date=date(2026, 10, 1),
                items=[
                    DraftItem(title="武侯祠", category="attraction", candidate_ref=1, indoor=True)
                ],
            ),
            DraftDay(date=date(2026, 10, 2), items=[]),
        ]
    )
    result = hydrate_plan(draft, _brief(), _catalog(), weather, None)
    item = result.plan.days[0].items[0]
    assert item.weather_adjusted is True


def test_sources_deduped_globally() -> None:
    draft = PlanDraft(
        days=[
            DraftDay(
                date=date(2026, 10, 1),
                items=[
                    DraftItem(title="武侯祠", category="attraction", candidate_ref=1),
                    DraftItem(title="锦里", category="attraction", candidate_ref=2),
                ],
            ),
            DraftDay(
                date=date(2026, 10, 2),
                items=[DraftItem(title="武侯祠再访", category="attraction", candidate_ref=1)],
            ),
        ]
    )
    result = hydrate_plan(draft, _brief(), _catalog(), {}, None)
    assert len(result.plan.sources) == len({str(url) for url in result.plan.sources})
    assert "https://example.com/a" in {str(url) for url in result.plan.sources}


def test_draft_day_count_mismatch_warns() -> None:
    short = PlanDraft(days=[DraftDay(date=date(2026, 10, 1), items=[])])
    result = hydrate_plan(short, _brief(), _catalog(), {}, None)
    assert any("1/2" in warning for warning in result.warnings)

    long = PlanDraft(
        days=[DraftDay(date=date(2026, 10, 1) + timedelta(days=i), items=[]) for i in range(3)]
    )
    result2 = hydrate_plan(long, _brief(), _catalog(), {}, None)
    assert any("截断" in warning for warning in result2.warnings)


def test_climate_reference_flag_and_item_ids() -> None:
    weather = {
        date(2026, 10, 1): DailyWeather(
            date=date(2026, 10, 1), temp_min=10, temp_max=18, condition="晴", climate=True
        )
    }
    draft = PlanDraft(
        days=[
            DraftDay(
                date=date(2026, 10, 1),
                items=[DraftItem(title="武侯祠", category="attraction", candidate_ref=1)],
            ),
            DraftDay(date=date(2026, 10, 2), items=[]),
        ]
    )
    city = GeoPoint(lat=30.1, lng=104.1)
    result = hydrate_plan(draft, _brief(), _catalog(), weather, city)
    assert result.plan.climate_reference is True
    assert result.plan.city_location == city
    assert result.plan.days[0].items[0].item_id == "d1-1"
    assert result.plan.plan_id
