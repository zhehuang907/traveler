"""Open-Meteo 免 Key 天气兜底 Provider，兼做超窗日期的历史气候参考。"""

from datetime import date, time
from typing import Any, cast

import httpx

from travel_agent.config import Settings
from travel_agent.domain.models import DailyWeather, GeoPoint, WeatherForecast
from travel_agent.services.http_client import build_client
from travel_agent.services.weather.base import ensure_within_window
from travel_agent.services.weather.wmo import describe_wmo

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_FORECAST_FIELDS = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "uv_index_max",
    "sunrise",
    "sunset",
]
# 归档接口无降水概率/紫外线，缺失字段在模型中保持 None
_CLIMATE_FIELDS = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "wind_speed_10m_max",
    "sunrise",
    "sunset",
]


class OpenMeteoProvider:
    """预报窗口 16 天；超窗时用 archive 的去年同期数据作气候参考。"""

    name = "openmeteo"
    forecast_days = 16

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return True

    async def forecast(self, city: str, start: date, end: date) -> WeatherForecast:
        ensure_within_window(self.name, start, date.today(), self.forecast_days)
        point, matched = await self._geocode(city)
        params: dict[str, str | float] = {
            "latitude": point.lat,
            "longitude": point.lng,
            "daily": ",".join(_FORECAST_FIELDS),
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        body = await self._request_raw(_FORECAST_URL, params)
        days = self._parse_daily(body, climate=False, count=(end - start).days + 1)
        return WeatherForecast(city=matched, location=point, days=days, provider=self.name)

    async def climate(self, city: str, start: date, end: date) -> WeatherForecast:
        """超预报窗口：取去年同期历史值作气候参考（标记 climate=True）。"""
        point, matched = await self._geocode(city)
        params: dict[str, str | float] = {
            "latitude": point.lat,
            "longitude": point.lng,
            "daily": ",".join(_CLIMATE_FIELDS),
            "timezone": "auto",
            "start_date": _shift_year(start).isoformat(),
            "end_date": _shift_year(end).isoformat(),
        }
        body = await self._request_raw(_ARCHIVE_URL, params)
        days = self._parse_daily(body, climate=True, count=(end - start).days + 1)
        return WeatherForecast(
            city=matched,
            location=point,
            days=days,
            provider=self.name,
            is_climate_reference=True,
        )

    async def _geocode(self, city: str) -> tuple[GeoPoint, str]:
        params: dict[str, str | float] = {
            "name": city,
            "count": 1,
            "language": "zh",
            "format": "json",
        }
        body = await self._request_raw(_GEOCODE_URL, params)
        results = body.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise httpx.HTTPError(f"Open-Meteo 未定位到城市: {city}")
        first = results[0]
        point = GeoPoint(lat=float(first["latitude"]), lng=float(first["longitude"]))
        return point, str(first.get("name", city))

    async def _request_raw(self, url: str, params: dict[str, str | float]) -> dict[str, object]:
        async with build_client(self._settings) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return cast(dict[str, object], response.json())

    def _parse_daily(
        self, body: dict[str, object], *, climate: bool, count: int
    ) -> list[DailyWeather]:
        daily = body.get("daily")
        if not isinstance(daily, dict):
            return []
        times = _as_str_list(daily.get("time"))
        raw_codes = daily.get("weather_code")
        codes: list[Any] = raw_codes if isinstance(raw_codes, list) else []
        temps_max = _as_float_list(daily.get("temperature_2m_max"))
        temps_min = _as_float_list(daily.get("temperature_2m_min"))
        precip = _as_int_list(daily.get("precipitation_probability_max"))
        winds = _as_float_list(daily.get("wind_speed_10m_max"))
        uvs = _as_float_list(daily.get("uv_index_max"))
        sunrises = _as_str_list(daily.get("sunrise"))
        sunsets = _as_str_list(daily.get("sunset"))
        result: list[DailyWeather] = []
        for i in range(min(len(times), count)):
            result.append(
                DailyWeather(
                    date=date.fromisoformat(times[i]),
                    temp_max=temps_max[i],
                    temp_min=temps_min[i],
                    condition=describe_wmo(_safe_index(codes, i)),
                    precip_prob=None if climate else _safe_index(precip, i),
                    wind_speed=_safe_index(winds, i),
                    uv_index=None if climate else _safe_index(uvs, i),
                    sunrise=_parse_clock(_safe_index(sunrises, i)),
                    sunset=_parse_clock(_safe_index(sunsets, i)),
                    climate=climate,
                )
            )
        return result


def _shift_year(value: date) -> date:
    """历史同期：整体前移一年（2/29 自动落到 2/28）。"""
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def _parse_clock(value: object) -> time | None:
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        return time.fromisoformat(value.split("T", 1)[1][:5])
    except ValueError:
        return None


def _as_str_list(value: object) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _as_float_list(value: object) -> list[float]:
    return [float(item) for item in value] if isinstance(value, list) else []


def _as_int_list(value: object) -> list[int | None]:
    if not isinstance(value, list):
        return []
    return [None if item is None else int(item) for item in value]


def _safe_index(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None
