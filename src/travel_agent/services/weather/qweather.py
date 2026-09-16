"""和风天气 Provider（国内首选，7 日逐日预报）。"""

from datetime import date
from typing import cast

from travel_agent.config import Settings
from travel_agent.domain.models import DailyWeather, GeoPoint, WeatherForecast
from travel_agent.services.errors import ProviderError
from travel_agent.services.http_client import build_client
from travel_agent.services.weather.base import ensure_within_window

_GEO_URL = "https://geoapi.qweather.com/v2/city/lookup"


class QWeatherProvider:
    """免费订阅默认 devapi 主机；新项目可能需在控制台查看专属 API Host。"""

    name = "qweather"
    forecast_days = 7

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.qweather_configured

    async def forecast(self, city: str, start: date, end: date) -> WeatherForecast:
        ensure_within_window(self.name, start, date.today(), self.forecast_days)
        point, matched = await self._lookup(city)
        params = {"location": point.amap_coord(), "key": self._key()}
        body = await self._get(f"{self._settings.qweather_api_host}/v7/weather/7d", params)
        if body.get("code") != "200":
            raise ProviderError(f"和风天气返回错误码 {body.get('code')}")
        days = self._parse(body, start, end)
        return WeatherForecast(city=matched, location=point, days=days, provider=self.name)

    async def _lookup(self, city: str) -> tuple[GeoPoint, str]:
        body = await self._get(_GEO_URL, {"location": city, "key": self._key()})
        if body.get("code") != "200":
            raise ProviderError(f"和风城市定位失败，错误码 {body.get('code')}")
        locations = body.get("location")
        if not isinstance(locations, list) or not locations:
            raise ProviderError(f"和风未定位到城市: {city}")
        first = locations[0]
        point = GeoPoint(lat=float(first["lat"]), lng=float(first["lon"]))
        return point, str(first.get("name", city))

    async def _get(self, url: str, params: dict[str, str]) -> dict[str, object]:
        async with build_client(self._settings) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return cast(dict[str, object], response.json())

    def _key(self) -> str:
        return self._settings.qweather_api_key.get_secret_value()

    def _parse(self, body: dict[str, object], start: date, end: date) -> list[DailyWeather]:
        raw_items = body.get("daily")
        if not isinstance(raw_items, list):
            return []
        wanted = {
            date.fromordinal(ordinal) for ordinal in range(start.toordinal(), end.toordinal() + 1)
        }
        days: list[DailyWeather] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            fx_date = date.fromisoformat(str(item["fxDate"]))
            if fx_date not in wanted:
                continue
            days.append(
                DailyWeather(
                    date=fx_date,
                    temp_max=float(item["tempMax"]),
                    temp_min=float(item["tempMin"]),
                    condition=str(item.get("textDay", "未知")),
                    # 7 日接口不提供降水概率/紫外线/日出日落，保持 None
                    precip_prob=None,
                    wind_speed=_to_float(item.get("windSpeedDay")),
                    uv_index=None,
                    sunrise=None,
                    sunset=None,
                )
            )
        return days


def _to_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
