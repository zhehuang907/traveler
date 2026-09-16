"""天气能力服务：降级链 + 缓存（1h）+ 超窗气候参考。"""

from collections.abc import Callable
from datetime import date, timedelta

from travel_agent.config import Settings
from travel_agent.domain.models import WeatherForecast
from travel_agent.logging_conf import get_logger
from travel_agent.services.base import run_chain
from travel_agent.services.cache import TTLCache, stable_key
from travel_agent.services.weather.base import WeatherProvider
from travel_agent.services.weather.openmeteo import OpenMeteoProvider
from travel_agent.services.weather.qweather import QWeatherProvider

log = get_logger(component="weather-service")
_MAX_CITY_LEN = 60


class WeatherService:
    """对外唯一天气入口；超预报窗口自动切历史气候参考。"""

    def __init__(self, settings: Settings, cache: TTLCache | None = None) -> None:
        self._settings = settings
        self._cache = cache or TTLCache(settings)
        # 必须先于 _build_providers 构造：registry 的 openmeteo 工厂闭包引用它
        self._openmeteo = OpenMeteoProvider(settings)
        self._providers = self._build_providers(settings.weather_provider_chain)

    def _build_providers(self, chain: list[str]) -> list[WeatherProvider]:
        registry: dict[str, Callable[[], WeatherProvider]] = {
            "qweather": lambda: QWeatherProvider(self._settings),
            "openmeteo": lambda: self._openmeteo,
        }
        providers: list[WeatherProvider] = []
        for name in chain:
            factory = registry.get(name)
            if factory is None:
                log.warning("unknown_weather_provider_ignored", provider=name)
                continue
            providers.append(factory())
        return providers

    async def get_weather(self, city: str, start: date, end: date) -> WeatherForecast:
        """返回 [start, end] 逐日天气；超窗返回 is_climate_reference=True 的气候参考。"""
        normalized = city.strip()
        if not normalized or len(normalized) > _MAX_CITY_LEN:
            raise ValueError(f"city 长度必须在 1..{_MAX_CITY_LEN} 之间")
        if start > end:
            raise ValueError("start 不能晚于 end")
        key = stable_key(
            "weather",
            provider=">".join(self._settings.weather_provider_chain),
            city=normalized,
            start=start.isoformat(),
            end=end.isoformat(),
        )

        async def fetch() -> WeatherForecast:
            if self._use_climate(start, end):
                log.info("climate_reference", city=normalized)
                return await self._openmeteo.climate(normalized, start, end)
            return await run_chain(
                "weather",
                self._providers,
                lambda p: p.forecast(normalized, start, end),
                self._settings,
            )

        forecast, _hit = await self._cache.get_or_set(key, self._settings.cache_ttl_weather, fetch)
        return forecast

    def _use_climate(self, start: date, end: date) -> bool:
        today = date.today()
        if end < today:
            return True  # 历史日期只能走归档
        horizon = max(p.forecast_days for p in self._providers)
        return start > today + timedelta(days=horizon - 1)
