"""天气 Provider 与 WeatherService 集成测试（respx mock）。"""

from datetime import date, timedelta

import httpx
import pytest
import respx
from pydantic import SecretStr

from travel_agent.config import Settings
from travel_agent.services.errors import (
    AllProvidersFailed,
    OutsideForecastWindow,
    ProviderError,
)
from travel_agent.services.weather.openmeteo import OpenMeteoProvider
from travel_agent.services.weather.qweather import QWeatherProvider
from travel_agent.services.weather.service import WeatherService

_GEO_URL = "https://geoapi.qweather.com/v2/city/lookup"
_7D_URL = "https://devapi.qweather.com/v7/weather/7d"
_OM_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_OM_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_OM_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def _fast_retry(settings: Settings, **updates: object) -> Settings:
    updates.setdefault("external_retry_base_delay", 0.001)
    return settings.model_copy(update=updates)


# ---------------- 和风天气 ----------------


@respx.mock
async def test_qweather_forecast_parses_and_filters_window(settings: Settings) -> None:
    s = _fast_retry(settings, qweather_api_key=SecretStr("qw"))
    today = date.today()
    tomorrow = today + timedelta(days=1)
    outside = today + timedelta(days=5)
    geo_route = respx.get(_GEO_URL).respond(
        200, json={"code": "200", "location": [{"name": "北京", "lat": "39.90", "lon": "116.39"}]}
    )
    daily_route = respx.get(_7D_URL).respond(
        200,
        json={
            "code": "200",
            "daily": [
                {
                    "fxDate": today.isoformat(),
                    "tempMax": "25",
                    "tempMin": "15",
                    "textDay": "晴",
                    "windSpeedDay": "12",
                },
                {
                    "fxDate": tomorrow.isoformat(),
                    "tempMax": "24",
                    "tempMin": "16",
                    "textDay": "多云",
                    "windSpeedDay": "8",
                },
                {
                    "fxDate": outside.isoformat(),
                    "tempMax": "20",
                    "tempMin": "10",
                    "textDay": "雨",
                    "windSpeedDay": "3",
                },
            ],
        },
    )
    provider = QWeatherProvider(s)
    assert provider.configured is True
    forecast = await provider.forecast("北京", today, tomorrow)
    assert forecast.city == "北京"
    assert forecast.provider == "qweather"
    assert forecast.location is not None
    assert (forecast.location.lat, forecast.location.lng) == (39.9, 116.39)
    assert len(forecast.days) == 2
    day = forecast.days[0]
    assert (day.temp_max, day.temp_min, day.condition) == (25.0, 15.0, "晴")
    assert day.precip_prob is None and day.uv_index is None
    assert day.sunrise is None and day.sunset is None
    # 和风坐标顺序：经度,纬度
    assert geo_route.calls.last.request.url.params["location"] == "北京"
    assert daily_route.calls.last.request.url.params["location"] == "116.39,39.9"


@respx.mock
async def test_qweather_geo_error_raises(settings: Settings) -> None:
    s = _fast_retry(settings, qweather_api_key=SecretStr("qw"))
    respx.get(_GEO_URL).respond(200, json={"code": "401"})
    with pytest.raises(ProviderError, match="城市定位失败"):
        await QWeatherProvider(s).forecast("未知地", date.today(), date.today())


@respx.mock
async def test_qweather_empty_location_raises(settings: Settings) -> None:
    s = _fast_retry(settings, qweather_api_key=SecretStr("qw"))
    respx.get(_GEO_URL).respond(200, json={"code": "200", "location": []})
    with pytest.raises(ProviderError, match="未定位到城市"):
        await QWeatherProvider(s).forecast("xx", date.today(), date.today())


@respx.mock
async def test_qweather_forecast_error_code_raises(settings: Settings) -> None:
    s = _fast_retry(settings, qweather_api_key=SecretStr("qw"))
    respx.get(_GEO_URL).respond(
        200, json={"code": "200", "location": [{"name": "x", "lat": "1", "lon": "1"}]}
    )
    respx.get(_7D_URL).respond(200, json={"code": "500"})
    with pytest.raises(ProviderError, match="错误码 500"):
        await QWeatherProvider(s).forecast("x", date.today(), date.today())


# ---------------- Open-Meteo ----------------


def _om_geo_response() -> object:
    return {"results": [{"name": "Beijing", "latitude": 39.9, "longitude": 116.4}]}


@respx.mock
async def test_openmeteo_forecast_parses_daily_fields(settings: Settings) -> None:
    today = date.today()
    tomorrow = today + timedelta(days=1)
    geo_route = respx.get(_OM_GEOCODE_URL).respond(200, json=_om_geo_response())
    forecast_route = respx.get(_OM_FORECAST_URL).respond(
        200,
        json={
            "daily": {
                "time": [today.isoformat(), tomorrow.isoformat()],
                "weather_code": [0, 61],
                "temperature_2m_max": [25.0, 22.0],
                "temperature_2m_min": [15.0, 14.0],
                "precipitation_probability_max": [10, 80],
                "wind_speed_10m_max": [5.0, 7.0],
                "uv_index_max": [4.0, 1.0],
                "sunrise": [f"{today.isoformat()}T06:10", f"{tomorrow.isoformat()}T06:11"],
                "sunset": [f"{today.isoformat()}T18:10", f"{tomorrow.isoformat()}T18:08"],
            }
        },
    )
    forecast = await OpenMeteoProvider(settings).forecast("beijing", today, tomorrow)
    assert forecast.city == "Beijing"
    assert forecast.provider == "openmeteo"
    assert forecast.is_climate_reference is False
    first, second = forecast.days
    assert first.condition == "晴" and first.rainy is False
    assert second.condition == "小雨" and second.rainy is True
    assert second.precip_prob == 80
    assert first.sunrise is not None and first.sunrise.hour == 6
    params = forecast_route.calls.last.request.url.params
    assert params["start_date"] == today.isoformat()
    assert params["latitude"] == "39.9"
    assert geo_route.calls.last.request.url.params["name"] == "beijing"


@respx.mock
async def test_openmeteo_climate_uses_shifted_archive_dates(settings: Settings) -> None:
    start = date.today() + timedelta(days=30)
    end = start + timedelta(days=1)
    respx.get(_OM_GEOCODE_URL).respond(200, json=_om_geo_response())
    archive_route = respx.get(_OM_ARCHIVE_URL).respond(
        200,
        json={
            "daily": {
                "time": [start.replace(year=start.year - 1).isoformat()],
                "weather_code": [3],
                "temperature_2m_max": [20.0],
                "temperature_2m_min": [10.0],
                "wind_speed_10m_max": [4.0],
                "sunrise": ["2025-01-01T07:00"],
                "sunset": ["2025-01-01T17:00"],
            }
        },
    )
    forecast = await OpenMeteoProvider(settings).climate("beijing", start, end)
    assert forecast.is_climate_reference is True
    day = forecast.days[0]
    assert day.climate is True
    assert day.precip_prob is None and day.uv_index is None
    assert day.condition == "阴"
    params = archive_route.calls.last.request.url.params
    assert params["start_date"] == start.replace(year=start.year - 1).isoformat()
    assert params["end_date"] == end.replace(year=end.year - 1).isoformat()


@respx.mock
async def test_openmeteo_geocode_miss_raises(settings: Settings) -> None:
    respx.get(_OM_GEOCODE_URL).respond(200, json={"results": []})
    with pytest.raises(httpx.HTTPError, match="未定位到城市"):
        await OpenMeteoProvider(settings).forecast("zzz", date.today(), date.today())


@respx.mock
async def test_openmeteo_outside_window_raises_before_http(settings: Settings) -> None:
    future = date.today() + timedelta(days=40)
    # 未注册任何路由：一旦发出 HTTP 请求，respx 会立即失败
    with pytest.raises(OutsideForecastWindow):
        await OpenMeteoProvider(settings).forecast("x", future, future)


@respx.mock
async def test_openmeteo_500_fails_fast_at_provider_level(settings: Settings) -> None:
    # Provider 自身不重试；重试统一在 run_chain 层
    respx.get(_OM_GEOCODE_URL).respond(200, json=_om_geo_response())
    route = respx.get(_OM_FORECAST_URL).respond(500)
    with pytest.raises(httpx.HTTPStatusError):
        await OpenMeteoProvider(settings).forecast("x", date.today(), date.today())
    assert route.call_count == 1


@respx.mock
async def test_service_retries_openmeteo_500_then_fails(settings: Settings) -> None:
    s = _fast_retry(settings, weather_provider_chain=["openmeteo"])
    respx.get(_OM_GEOCODE_URL).respond(200, json=_om_geo_response())
    route = respx.get(_OM_FORECAST_URL)
    route.side_effect = [httpx.Response(500) for _ in range(3)]
    with pytest.raises(AllProvidersFailed):
        await WeatherService(s).get_weather("x", date.today(), date.today())
    assert route.call_count == 3


# ---------------- WeatherService ----------------


@respx.mock
async def test_service_falls_back_to_openmeteo_and_caches(settings: Settings) -> None:
    s = _fast_retry(settings)
    geo_route = respx.get(_OM_GEOCODE_URL).respond(200, json=_om_geo_response())
    forecast_route = respx.get(_OM_FORECAST_URL).respond(
        200,
        json={
            "daily": {
                "time": [date.today().isoformat()],
                "weather_code": [0],
                "temperature_2m_max": [20.0],
                "temperature_2m_min": [10.0],
                "precipitation_probability_max": [0],
                "wind_speed_10m_max": [2.0],
                "uv_index_max": [3.0],
                "sunrise": [None],
                "sunset": [None],
            }
        },
    )
    service = WeatherService(s)
    today = date.today()
    first = await service.get_weather("beijing", today, today)
    second = await service.get_weather("beijing", today, today)
    assert first.provider == "openmeteo"
    assert len(second.days) == 1
    assert geo_route.call_count == 1
    assert forecast_route.call_count == 1


@respx.mock
async def test_service_future_beyond_window_uses_climate(settings: Settings) -> None:
    s = _fast_retry(settings)
    start = date.today() + timedelta(days=20)  # 超过 16 天窗口
    respx.get(_OM_GEOCODE_URL).respond(200, json=_om_geo_response())
    archive_route = respx.get(_OM_ARCHIVE_URL).respond(
        200,
        json={
            "daily": {
                "time": [start.replace(year=start.year - 1).isoformat()],
                "weather_code": [1],
                "temperature_2m_max": [19.0],
                "temperature_2m_min": [9.0],
                "wind_speed_10m_max": [3.0],
                "sunrise": [None],
                "sunset": [None],
            }
        },
    )
    forecast = await WeatherService(s).get_weather("beijing", start, start)
    assert forecast.is_climate_reference is True
    assert archive_route.called


@respx.mock
async def test_service_past_date_uses_climate(settings: Settings) -> None:
    s = _fast_retry(settings)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=1)
    respx.get(_OM_GEOCODE_URL).respond(200, json=_om_geo_response())
    archive_route = respx.get(_OM_ARCHIVE_URL).respond(
        200,
        json={
            "daily": {
                "time": [start.replace(year=start.year - 1).isoformat()],
                "weather_code": [2],
                "temperature_2m_max": [19.0],
                "temperature_2m_min": [9.0],
                "wind_speed_10m_max": [3.0],
                "sunrise": [None],
                "sunset": [None],
            }
        },
    )
    forecast = await WeatherService(s).get_weather("beijing", start, end)
    assert forecast.is_climate_reference is True
    assert archive_route.called


@pytest.mark.parametrize(
    ("city", "start", "end"),
    [
        ("   ", date(2026, 10, 1), date(2026, 10, 2)),
        ("北京", date(2026, 10, 3), date(2026, 10, 1)),
    ],
)
async def test_service_validates_arguments(
    settings: Settings, city: str, start: date, end: date
) -> None:
    with pytest.raises(ValueError):
        await WeatherService(settings).get_weather(city, start, end)


@respx.mock
async def test_service_qweather_first_when_configured(settings: Settings) -> None:
    s = _fast_retry(settings, qweather_api_key=SecretStr("qw"))
    today = date.today()
    respx.get(_GEO_URL).respond(
        200, json={"code": "200", "location": [{"name": "北京", "lat": "39.9", "lon": "116.4"}]}
    )
    respx.get(_7D_URL).respond(
        200,
        json={
            "code": "200",
            "daily": [
                {"fxDate": today.isoformat(), "tempMax": "25", "tempMin": "15", "textDay": "晴"},
            ],
        },
    )
    # Open-Meteo 路由不注册：若降级到 openmeteo，respx 会立即报错
    forecast = await WeatherService(s).get_weather("北京", today, today)
    assert forecast.provider == "qweather"
