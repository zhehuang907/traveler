"""天气能力包：和风 / Open-Meteo 降级链 + 气候参考。"""

from travel_agent.services.weather.base import WeatherProvider, ensure_within_window
from travel_agent.services.weather.openmeteo import OpenMeteoProvider
from travel_agent.services.weather.qweather import QWeatherProvider
from travel_agent.services.weather.service import WeatherService
from travel_agent.services.weather.wmo import describe_wmo

__all__ = [
    "OpenMeteoProvider",
    "QWeatherProvider",
    "WeatherProvider",
    "WeatherService",
    "describe_wmo",
    "ensure_within_window",
]
