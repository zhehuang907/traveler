"""搜索能力包：Tavily / 博查 / DuckDuckGo 降级链。"""

from travel_agent.services.search.base import SearchProvider, SearchTimeRange, parse_date
from travel_agent.services.search.bocha import BochaSearchProvider
from travel_agent.services.search.duckduckgo import DuckDuckGoSearchProvider
from travel_agent.services.search.service import SearchService
from travel_agent.services.search.tavily import TavilySearchProvider

__all__ = [
    "BochaSearchProvider",
    "DuckDuckGoSearchProvider",
    "SearchProvider",
    "SearchService",
    "SearchTimeRange",
    "TavilySearchProvider",
    "parse_date",
]
