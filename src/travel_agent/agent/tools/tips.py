"""search_tips 工具：目的地避坑/习俗/安全提示（网页搜索特化）。"""

from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.agent.tools.web_search import search_web
from travel_agent.domain.models import SearchResult


async def search_tips(ctx: ToolRegistry, city: str) -> list[SearchResult]:
    """目的地注意事项；资料只进 tips/备注，不产生具体商户事实。"""
    query = f"{city} 旅游攻略 避坑 注意事项 习俗 安全"
    return await search_web(ctx, query, max_results=5, freshness="")
