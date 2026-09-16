"""领域模型校验测试：纯内核，无 IO。"""

from datetime import date

import pytest
from pydantic import ValidationError

from travel_agent.domain.models import (
    DailyWeather,
    GeoPoint,
    Poi,
    RouteInfo,
    SearchResult,
    WeatherForecast,
)


def test_geo_point_coord_order() -> None:
    point = GeoPoint(lat=30.1, lng=120.2)
    # 高德参数序：经度,纬度；OSM 参数序：纬度,经度
    assert point.amap_coord() == "120.2,30.1"
    assert point.osm_coord() == "30.1,120.2"


@pytest.mark.parametrize(
    ("lat", "lng"),
    [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)],
)
def test_geo_point_rejects_out_of_range(lat: float, lng: float) -> None:
    with pytest.raises(ValidationError):
        GeoPoint(lat=lat, lng=lng)


def test_geo_point_is_frozen_and_forbids_extra() -> None:
    point = GeoPoint(lat=1.0, lng=2.0)
    with pytest.raises(ValidationError):
        point.lat = 3.0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        GeoPoint(lat=1.0, lng=2.0, gcj=True)  # type: ignore[call-arg]


def test_search_result_requires_title_url_provider() -> None:
    result = SearchResult(
        title="故宫开放公告",
        url="https://example.com/news",
        provider="tavily",
    )
    assert result.snippet == ""
    assert result.published_at is None
    assert str(result.url).rstrip("/") == "https://example.com/news"
    with pytest.raises(ValidationError):
        SearchResult(title="", url="https://example.com", provider="tavily")
    with pytest.raises(ValidationError):
        SearchResult(title="t", url="not-a-url", provider="tavily")
    with pytest.raises(ValidationError):
        SearchResult(title="t", url="https://example.com", provider="")


def test_poi_rating_bounds_and_defaults() -> None:
    poi = Poi(
        poi_id="p1",
        name="故宫",
        location=GeoPoint(lat=39.9, lng=116.4),
        sources=["https://www.amap.com/place/p1"],
        provider="amap",
    )
    assert poi.categories == []
    assert poi.rating is None
    assert poi.cost_hint is None
    Poi(
        poi_id="p2",
        name="x",
        rating=10.0,
        location=GeoPoint(lat=0.0, lng=0.0),
        provider="osm",
    )
    with pytest.raises(ValidationError):
        Poi(
            poi_id="p3",
            name="x",
            rating=10.1,
            location=GeoPoint(lat=0.0, lng=0.0),
            provider="osm",
        )


@pytest.mark.parametrize(
    ("prob", "rainy"),
    [(None, False), (0, False), (60, False), (61, True), (100, True)],
)
def test_daily_weather_rainy_threshold(prob: int | None, rainy: bool) -> None:
    day = DailyWeather(
        date=date(2026, 10, 1),
        temp_min=10.0,
        temp_max=20.0,
        condition="小雨",
        precip_prob=prob,
    )
    assert day.rainy is rainy
    assert day.climate is False


def test_daily_weather_rejects_bad_probability() -> None:
    with pytest.raises(ValidationError):
        DailyWeather(
            date=date(2026, 10, 1),
            temp_min=10.0,
            temp_max=20.0,
            condition="晴",
            precip_prob=101,
        )


def test_weather_forecast_defaults() -> None:
    forecast = WeatherForecast(city="北京", provider="openmeteo")
    assert forecast.days == []
    assert forecast.location is None
    assert forecast.is_climate_reference is False


def test_route_info_validation() -> None:
    origin = GeoPoint(lat=39.9, lng=116.4)
    destination = GeoPoint(lat=31.2, lng=121.5)
    route = RouteInfo(
        origin=origin,
        destination=destination,
        mode="driving",
        distance_m=1_200_000,
        duration_s=43_200,
        provider="amap",
    )
    assert route.mode == "driving"
    # pydantic mypy 插件不校验 Literal 取值，非法模式在运行期被拒绝
    with pytest.raises(ValidationError):
        RouteInfo(
            origin=origin,
            destination=destination,
            mode="flying",
            distance_m=1,
            duration_s=1,
            provider="amap",
        )
    with pytest.raises(ValidationError):
        RouteInfo(
            origin=origin,
            destination=destination,
            mode="walking",
            distance_m=-1,
            duration_s=1,
            provider="osm",
        )
