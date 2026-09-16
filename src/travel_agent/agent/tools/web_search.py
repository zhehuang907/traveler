"""search_web 工具：网页搜索（标题/摘要/URL/发布时间）。"""

from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.domain.models import SearchResult
from travel_agent.services.search.base import SearchTimeRange

# 与 Tavily/Bocha 时间窗对齐的 freshness 简写
_FRESHNESS: dict[str, SearchTimeRange | None] = {
    "d1": "day",
    "w1": "week",
    "m1": "month",
    "y1": "year",
    "": None,
}
_MAX_RESULTS = 10


async def search_web(
    ctx: ToolRegistry,
    query: str,
    max_results: int = 5,
    freshness: str = "y1",
) -> list[SearchResult]:
    """搜索网页；任何异常由注册表吞掉并降级为空列表。"""
    time_range = _FRESHNESS.get(freshness, "year")
    capped = min(max(1, max_results), _MAX_RESULTS)
    results = await ctx.run("search_web", lambda: ctx.search.search(query, capped, time_range))
    return results or []
