"""外部服务的结构化协议：生产 service 与测试替身共同满足。"""

from datetime import date
from typing import Protocol

from travel_agent.domain.models import (
    GeoPoint,
    Poi,
    RouteInfo,
    SearchResult,
    TravelMode,
    WeatherForecast,
)
from travel_agent.services.search.base import SearchTimeRange


class SearchBackend(Protocol):
    async def search(
        self,
        query: str,
        max_results: int,
        time_range: SearchTimeRange | None,
    ) -> list[SearchResult]:
        """网页检索。"""


class MapsBackend(Protocol):
    async def geocode(self, address: str) -> GeoPoint:
        """地址 -> 坐标。"""

    async def search_poi(
        self,
        city: str,
        keywords: str,
        category: str | None,
        limit: int,
    ) -> list[Poi]:
        """POI 文本搜索。"""

    async def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        mode: TravelMode,
    ) -> RouteInfo:
        """两点路径规划。"""


class WeatherBackend(Protocol):
    async def get_weather(self, city: str, start: date, end: date) -> WeatherForecast:
        """区间逐日天气/气候参考。"""
