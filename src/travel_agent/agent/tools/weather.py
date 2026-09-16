"""get_weather 工具：行程日期范围逐日天气（超窗自动转历史气候参考）。"""

from datetime import date

from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.domain.models import WeatherForecast


async def get_weather(
    ctx: ToolRegistry,
    city: str,
    start: date,
    end: date,
) -> WeatherForecast | None:
    """返回逐日预报/气候参考；全部 Provider 失败时返回 None。"""
    return await ctx.run("get_weather", lambda: ctx.weather.get_weather(city, start, end))
