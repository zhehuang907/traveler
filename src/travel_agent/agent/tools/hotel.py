"""search_hotel 工具：酒店候选（高德 POI，带预算关键词；阶段三特化检索）。"""

from datetime import date

from travel_agent.agent.tools.poi_search import search_poi
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.domain.models import Poi

_HOTEL_CATEGORY = "100000"  # 高德 POI 类型：住宿服务（大类编码）


async def search_hotel(
    ctx: ToolRegistry,
    city: str,
    checkin: date,
    checkout: date,
    budget_cny: float | None = None,
) -> list[Poi]:
    """检索住宿候选。日期用于未来接房价接口；当前以预算关键词做文本搜索。"""
    keyword = f"酒店 {budget_cny:.0f}元" if budget_cny is not None else "酒店"
    if checkin > checkout:
        raise ValueError("入住日期不能晚于退房日期")
    return await search_poi(ctx, city, keyword, category=_HOTEL_CATEGORY, limit=8)
