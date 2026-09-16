"""天气节点：拉取行程区间逐日天气，失败时节点产出空映射但不中断。"""

from travel_agent.agent.state import TravelState
from travel_agent.agent.tools import geocode, get_weather
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.domain.models import DailyWeather, GeoPoint


async def fetch_weather(state: TravelState, *, ctx: ToolRegistry) -> dict[str, object]:
    brief = state["brief"]
    # 路由保证只有 is_ready() 的 brief 进入本节点
    if brief.start_date is None or brief.end_date is None:
        raise RuntimeError("weather 节点要求 brief 日期齐备")
    forecast = await get_weather(ctx, brief.destination, brief.start_date, brief.end_date)
    if forecast is None:
        location: GeoPoint | None = state.get("city_location")
    else:
        weather = {daily.date.isoformat(): daily for daily in forecast.days}
        location = forecast.location or await geocode(ctx, brief.destination)
        return {
            "weather": weather,
            "city_location": location,
            "traces": ctx.trace_snapshot(),
        }
    empty: dict[str, DailyWeather] = {}
    return {"weather": empty, "city_location": location, "traces": ctx.trace_snapshot()}
