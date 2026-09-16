"""Agent 工具层：services 之上的统一工具协议（限流/缓存/降级在注册表内）。"""

from travel_agent.agent.tools.hotel import search_hotel
from travel_agent.agent.tools.poi_search import geocode, search_poi
from travel_agent.agent.tools.registry import IntervalLimiter, ToolRegistry
from travel_agent.agent.tools.route import get_route
from travel_agent.agent.tools.tips import search_tips
from travel_agent.agent.tools.weather import get_weather
from travel_agent.agent.tools.web_search import search_web

__all__ = [
    "IntervalLimiter",
    "ToolRegistry",
    "geocode",
    "get_route",
    "get_weather",
    "search_hotel",
    "search_poi",
    "search_tips",
    "search_web",
]
