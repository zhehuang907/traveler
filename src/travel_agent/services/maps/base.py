"""地图能力 Provider 协议：地理编码 / POI 搜索 / 路径规划。"""

from typing import Protocol

from travel_agent.domain.models import GeoPoint, Poi, RouteInfo, TravelMode


class MapsProvider(Protocol):
    """地图 Provider 契约。坐标体系由实现方保证同源一致。"""

    @property
    def name(self) -> str:
        """Provider 标识（amap/osm）。"""
        ...

    @property
    def configured(self) -> bool:
        """Key 是否就绪（OSM 免 Key 恒 True）。"""
        ...

    async def geocode(self, address: str) -> GeoPoint:
        """地址/城市名 -> 坐标，找不到时抛 ProviderError。"""
        ...

    async def search_poi(
        self,
        city: str,
        keywords: str,
        category: str | None,
        limit: int,
    ) -> list[Poi]:
        """城市内 POI 文本搜索。"""
        ...

    async def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        mode: TravelMode,
    ) -> RouteInfo:
        """两点间单方式路线，返回距离（米）与耗时（秒）。"""
        ...
