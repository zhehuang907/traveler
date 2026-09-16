"""get_route 工具：相邻条目通勤核实（距离/耗时，结果 7 天缓存）。"""

from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.domain.models import GeoPoint, RouteInfo, TravelMode


async def get_route(
    ctx: ToolRegistry,
    origin: GeoPoint,
    destination: GeoPoint,
    mode: TravelMode = "driving",
) -> RouteInfo | None:
    """返回单方式路线；失败降级 None，调用方跳过该项通勤校验。"""
    return await ctx.run("get_route", lambda: ctx.maps.route(origin, destination, mode))
