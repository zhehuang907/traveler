"""高德地图 Web 服务 Provider（地理编码 / POI / 路径，坐标 GCJ-02）。"""

from typing import cast

from pydantic import HttpUrl

from travel_agent.config import Settings
from travel_agent.domain.models import GeoPoint, Poi, RouteInfo, TravelMode
from travel_agent.services.errors import ProviderError
from travel_agent.services.http_client import build_client

_BASE_URL = "https://restapi.amap.com/v3"
# 统一出行方式 -> 高德路径接口
_ROUTE_PATHS = {
    "driving": "/direction/driving",
    "walking": "/direction/walking",
    "transit": "/direction/transit/integrated",
}


class AMapProvider:
    """国内首选；key 为空时由降级链跳过。"""

    name = "amap"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.amap_configured

    async def geocode(self, address: str) -> GeoPoint:
        body = await self._get("/geocode/geo", {"address": address})
        geocodes = body.get("geocodes")
        if not isinstance(geocodes, list) or not geocodes:
            raise ProviderError(f"高德未能地理编码: {address}")
        return _parse_coord(str(geocodes[0].get("location", "")))

    async def search_poi(
        self,
        city: str,
        keywords: str,
        category: str | None,
        limit: int,
    ) -> list[Poi]:
        params = {
            "keywords": keywords,
            "city": city,
            "citylimit": "true",
            "extensions": "all",
            "offset": str(limit),
        }
        if category:
            params["types"] = category
        body = await self._get("/place/text", params)
        raw_pois = body.get("pois")
        if not isinstance(raw_pois, list):
            return []
        return [item for item in (_parse_poi(raw) for raw in raw_pois) if item is not None]

    async def route(
        self,
        origin: GeoPoint,
        destination: GeoPoint,
        mode: TravelMode,
    ) -> RouteInfo:
        path = _ROUTE_PATHS.get(mode)
        if path is None:
            raise ProviderError(f"高德不支持出行方式: {mode}")
        params = {"origin": origin.amap_coord(), "destination": destination.amap_coord()}
        if mode == "transit":
            params["city"] = await self._city_code(origin)
        body = await self._get(path, params)
        route_body = body.get("route")
        distance, duration = _extract_route(route_body, mode)
        return RouteInfo(
            origin=origin,
            destination=destination,
            mode=mode,
            distance_m=distance,
            duration_s=duration,
            provider=self.name,
        )

    async def _city_code(self, point: GeoPoint) -> str:
        """公交路径需要城市码：逆地理编码取 adcode。"""
        body = await self._get("/geocode/regeo", {"location": point.amap_coord()})
        address = body.get("regeocode")
        if not isinstance(address, dict):
            raise ProviderError("高德逆地理编码失败，无法规划公交路径")
        component = address.get("addressComponent")
        if not isinstance(component, dict):
            raise ProviderError("高德逆地理编码缺少 addressComponent")
        return str(component.get("adcode", ""))

    async def _get(self, path: str, extra: dict[str, str]) -> dict[str, object]:
        params = {"key": self._settings.amap_api_key.get_secret_value(), **extra}
        async with build_client(self._settings) as client:
            response = await client.get(f"{_BASE_URL}{path}", params=params)
            response.raise_for_status()
            body = cast(dict[str, object], response.json())
        if str(body.get("status")) != "1":
            raise ProviderError(f"高德接口 {path} 返回 status={body.get('status')}")
        return body


def _parse_coord(value: str) -> GeoPoint:
    try:
        lng_text, lat_text = value.split(",", 1)
        return GeoPoint(lat=float(lat_text), lng=float(lng_text))
    except (ValueError, AttributeError) as exc:
        raise ProviderError(f"高德坐标格式非法: {value!r}") from exc


def _parse_poi(raw: object) -> Poi | None:
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("name"):
        return None
    location = raw.get("location")
    if not isinstance(location, str) or "[" in location:
        return None
    try:
        point = _parse_coord(location)
        raw_biz = raw.get("biz_ext")
        biz: dict[str, object] = raw_biz if isinstance(raw_biz, dict) else {}
        open_time = biz.get("open_time")
        poi_id = str(raw["id"])
        return Poi(
            poi_id=poi_id,
            name=str(raw["name"]),
            categories=[str(raw["type"])] if raw.get("type") else [],
            address=str(raw.get("address", "")),
            location=point,
            rating=_to_rating(biz.get("rating")),
            business_hours=str(open_time) if isinstance(open_time, str) else None,
            sources=[HttpUrl(f"https://www.amap.com/place/{poi_id}")],
            provider="amap",
        )
    except (ProviderError, ValueError):
        return None


def _to_rating(value: object) -> float | None:
    try:
        rating = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # 高德空评分常返回 [] 或空串；满分 5 分
    return rating if 0.0 < rating <= 5.0 else None


def _extract_route(route_body: object, mode: TravelMode) -> tuple[int, int]:
    if not isinstance(route_body, dict):
        raise ProviderError("高德路径返回缺少 route")
    if mode == "transit":
        transits = route_body.get("transits")
        if not isinstance(transits, list) or not transits:
            raise ProviderError("高德未返回公交方案")
        first = transits[0]
        return _to_int(first, "distance"), _to_int(first, "duration")
    paths = route_body.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ProviderError("高德未返回路径方案")
    return _to_int(paths[0], "distance"), _to_int(paths[0], "duration")


def _to_int(body: object, key: str) -> int:
    if not isinstance(body, dict):
        raise ProviderError("高德路径方案格式非法")
    try:
        return int(float(body[key]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError(f"高德路径缺少字段: {key}") from exc
