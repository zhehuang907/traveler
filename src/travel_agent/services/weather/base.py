"""天气 Provider 协议与日期窗口工具。"""

from datetime import date, timedelta
from typing import Protocol

from travel_agent.domain.models import WeatherForecast
from travel_agent.services.errors import OutsideForecastWindow


class WeatherProvider(Protocol):
    """逐日预报 Provider。"""

    @property
    def name(self) -> str:
        """Provider 标识（qweather/openmeteo）。"""
        ...

    forecast_days: int

    @property
    def configured(self) -> bool:
        """Key 是否就绪（免 Key Provider 恒 True）。"""
        ...

    async def forecast(self, city: str, start: date, end: date) -> WeatherForecast:
        """窗口内逐日预报；窗口覆盖不了时抛 OutsideForecastWindow。"""
        ...


def ensure_within_window(provider: str, start: date, today: date, horizon: int) -> None:
    """校验 [start, ...] 是否落在「今天起 horizon 天」预报窗口内。"""
    last_day = today + timedelta(days=horizon - 1)
    if start > last_day:
        raise OutsideForecastWindow(
            f"{provider} 预报窗口仅到 {last_day.isoformat()}，无法覆盖 {start.isoformat()}"
        )
