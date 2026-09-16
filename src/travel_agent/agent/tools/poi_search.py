"""search_poi / geocode 工具：高德 POI 文本搜索与地理编码（1 QPS 节流）。"""

from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.domain.models import GeoPoint, Poi

_MAX_POI_LIMIT = 20


async def search_poi(
    ctx: ToolRegistry,
    city: str,
    keywords: str,
    category: str | None = None,
    limit: int = 10,
) -> list[Poi]:
    """高德 POI：评分/坐标/营业时间；配额仅 100 次/日，节流 + 缓存 + 不炸断。"""
    capped = min(max(1, limit), _MAX_POI_LIMIT)

    async def call() -> list[Poi]:
        async with ctx.poi_limiter.throttle():
            return await ctx.maps.search_poi(city, keywords, category, capped)

    results = await ctx.run("search_poi", call)
    return results or []


async def geocode(ctx: ToolRegistry, address: str) -> GeoPoint | None:
    """地理编码取坐标（配额 5000/日，同样节流）；失败返回 None。"""

    async def call() -> GeoPoint:
        async with ctx.poi_limiter.throttle():
            return await ctx.maps.geocode(address)

    return await ctx.run("geocode", call)
