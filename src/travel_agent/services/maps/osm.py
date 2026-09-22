"""OpenStreetMap 免 Key 地图兜底：Nominatim 地理编码 + Overpass POI + OSRM 路径。

- 坐标 WGS-84，与 Leaflet/OSM 瓦片同源。
- 公交路径无免费接口，抛 UnsupportedTravelMode 由上层提示。
- 三个接口的调用政策均要求可识别 User-Agent（见 http_client.USER_AGENT）。
"""

from collections.abc import Mapping
from typing import cast

from pydantic import HttpUrl, ValidationError

from travel_agent.config import Settings
from travel_agent.domain.models import GeoPoint, Poi, RouteInfo, TravelMode
from travel_agent.services.errors import ProviderError, UnsupportedTravelMode
from travel_agent.services.http_client import build_client

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OSRM_BASE = "https://routing.openstreetmap.de"
_POI_RADIUS_M = 8000
_OSM_PROFILE = {"driving": "routed-car", "walking": "routed-foot"}


class OsmMapsProvider:
    """全球可用、免 Key、数据密度低于高德，作为降级链兜底。"""

    name = "osm"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return True

    async def geocode(self, address: str) -> GeoPoint:
        params: dict[str, str | int] = {
            "q": address,
            "format": "jsonv2",
            "limit": 1,
            "accept-language": "zh",
        }
        body = await self._get_json(_NOMINATIM_URL, params)
        if not isinstance(body, list) or not body or not isinstance(body[0], dict):
            raise ProviderError(f"Nominatim 未能地理编码: {address}")
        first = body[0]
        return GeoPoint(lat=float(first["lat"]), lng=float(first["lon"]))

    async def search_poi(
        self,
        city: str,
        keywords: str,
        category: str | None,
        limit: int,
    ) -> list[Poi]:
        center = await self.geocode(city)
        query = _build_overpass(center, keywords, limit)
        async with build_client(self._settings) as client:
            response = await client.post(_OVERPASS_URL, data={"data": query})
            response.raise_for_status()
            body = cast(dict[str, object], response.json())
        elements = body.get("elements")
        if not isinstance(elements, list):
            return []
        return [item for item in (_element_to_poi(raw) for raw in elements) if item is not None]

    async def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        mode: TravelMode,
    ) -> RouteInfo:
        profile = _OSM_PROFILE.get(mode)
        if profile is None:
            raise UnsupportedTravelMode("OSM 兜底不支持公交路径，请配置高德 Key")
        coords = f"{origin.lng},{origin.lat};{destination.lng},{destination.lat}"
        url = f"{_OSRM_BASE}/{profile}/route/v1/driving/{coords}"
        body = await self._get_json(url, {"overview": "false", "steps": "false"})
        if not isinstance(body, dict) or body.get("code") != "Ok":
            raise ProviderError("OSRM 路径失败或返回格式异常")
        routes = body.get("routes")
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise ProviderError("OSRM 未返回路径")
        first = routes[0]
        return RouteInfo(
            origin=origin,
            destination=destination,
            mode=mode,
            distance_m=int(float(first["distance"])),
            duration_s=int(float(first["duration"])),
            provider=self.name,
        )

    async def _get_json(self, url: str, params: Mapping[str, str | int | float]) -> object:
        async with build_client(self._settings) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()


def _build_overpass(center: GeoPoint, keywords: str, limit: int) -> str:
    """按名称模糊匹配城市半径内的 node/way/relation。"""
    safe_keywords = keywords.replace('"', " ").replace("\\", " ")
    around = f"around:{_POI_RADIUS_M},{center.lat},{center.lng}"
    return (
        f'[out:json][timeout:25];(nwr({around})["name"~"{safe_keywords}",i];);out center {limit};'
    )


def _element_to_poi(raw: object) -> Poi | None:
    if not isinstance(raw, dict):
        return None
    element_id = raw.get("id")
    element_type = raw.get("type")
    tags = raw.get("tags")
    if (
        element_id is None
        or element_type not in {"node", "way", "relation"}
        or not isinstance(tags, dict)
    ):
        return None
    point = _element_point(raw)
    if point is None:
        return None
    categories = [str(v) for k, v in tags.items() if k in {"amenity", "tourism", "shop"}]
    try:
        return Poi(
            poi_id=f"{element_type}/{element_id}",
            name=str(tags.get("name", "")),
            categories=categories,
            address=str(tags.get("addr:full", "")),
            location=point,
            rating=None,
            business_hours=_opt_str(tags.get("opening_hours")),
            sources=[HttpUrl(f"https://www.openstreetmap.org/{element_type}/{element_id}")],
            provider="osm",
        )
    except ValidationError:
        return None


def _element_point(raw: dict[str, object]) -> GeoPoint | None:
    lat = raw.get("lat")
    lon = raw.get("lon")
    if isinstance(lat, int | float) and isinstance(lon, int | float):
        return GeoPoint(lat=float(lat), lng=float(lon))
    center = raw.get("center")
    if isinstance(center, dict):
        clat = center.get("lat")
        clon = center.get("lon")
        if isinstance(clat, int | float) and isinstance(clon, int | float):
            return GeoPoint(lat=float(clat), lng=float(clon))
    return None


def _opt_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value.strip() else None
