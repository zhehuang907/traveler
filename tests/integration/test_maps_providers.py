"""高德 / OSM 地图 Provider 集成测试（respx mock，禁止真实网络）。"""

from urllib.parse import parse_qs

import httpx
import pytest
import respx
from pydantic import SecretStr

from travel_agent.config import Settings
from travel_agent.domain.models import GeoPoint, TravelMode
from travel_agent.services.errors import ProviderError, UnsupportedTravelMode
from travel_agent.services.maps.amap import AMapProvider
from travel_agent.services.maps.osm import OsmMapsProvider

_AMAP_BASE = "https://restapi.amap.com/v3"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OSRM_CAR = "https://routing.openstreetmap.de/routed-car/route/v1/driving/"
_OSRM_FOOT = "https://routing.openstreetmap.de/routed-foot/route/v1/driving/"

_BEIJING = GeoPoint(lat=39.9, lng=116.4)
_SHANGHAI = GeoPoint(lat=31.2, lng=121.5)


# ---------------- 高德：地理编码 ----------------


@respx.mock
async def test_amap_geocode_parses_lnglat(settings: Settings) -> None:
    s = settings.model_copy(update={"amap_api_key": SecretStr("amap-key")})
    route = respx.get(f"{_AMAP_BASE}/geocode/geo").respond(
        200, json={"status": "1", "geocodes": [{"location": "116.39,39.90"}]}
    )
    point = await AMapProvider(s).geocode("北京")
    assert point == GeoPoint(lat=39.9, lng=116.39)
    assert route.calls.last.request.url.params["key"] == "amap-key"


@respx.mock
@pytest.mark.parametrize(
    "payload",
    [
        {"status": "0", "info": "INVALID_USER_KEY"},
        {"status": "1", "geocodes": []},
    ],
)
async def test_amap_geocode_failures(settings: Settings, payload: dict[str, object]) -> None:
    s = settings.model_copy(update={"amap_api_key": SecretStr("k")})
    respx.get(f"{_AMAP_BASE}/geocode/geo").respond(200, json=payload)
    with pytest.raises(ProviderError):
        await AMapProvider(s).geocode("xx")


# ---------------- 高德：POI ----------------


@respx.mock
async def test_amap_search_poi_parses_and_filters(settings: Settings) -> None:
    s = settings.model_copy(update={"amap_api_key": SecretStr("amap-key")})
    route = respx.get(f"{_AMAP_BASE}/place/text").respond(
        200,
        json={
            "status": "1",
            "pois": [
                {
                    "id": "B001",
                    "name": "故宫",
                    "type": "风景名胜;名胜古迹",
                    "address": "景山前街4号",
                    "location": "116.39,39.90",
                    "biz_ext": {"rating": "4.8", "open_time": "08:30-17:00"},
                },
                {"id": "", "name": "无ID", "location": "116.39,39.90"},
                "脏数据",
                {"id": "B002", "name": "坐标异常", "location": "[]"},
                {
                    "id": "B003",
                    "name": "广场",
                    "type": "",
                    "location": "116.40,39.91",
                    "biz_ext": {"rating": []},
                },
            ],
        },
    )
    pois = await AMapProvider(s).search_poi("北京", "景点", None, 10)
    assert [poi.poi_id for poi in pois] == ["B001", "B003"]
    gugong = pois[0]
    assert gugong.rating == 4.8
    assert gugong.business_hours == "08:30-17:00"
    assert gugong.categories == ["风景名胜;名胜古迹"]
    assert gugong.provider == "amap"
    assert str(gugong.sources[0]).rstrip("/") == "https://www.amap.com/place/B001"
    assert pois[1].rating is None
    assert pois[1].categories == []
    params = route.calls.last.request.url.params
    assert params["citylimit"] == "true"
    assert params["extensions"] == "all"
    assert params["offset"] == "10"


# ---------------- 高德：路线 ----------------


@pytest.mark.parametrize(
    ("mode", "path"),
    [("driving", "/direction/driving"), ("walking", "/direction/walking")],
)
@respx.mock
async def test_amap_route_driving_walking(settings: Settings, mode: TravelMode, path: str) -> None:
    s = settings.model_copy(update={"amap_api_key": SecretStr("k")})
    respx.get(f"{_AMAP_BASE}{path}").respond(
        200,
        json={"status": "1", "route": {"paths": [{"distance": "12000", "duration": "1800"}]}},
    )
    route = await AMapProvider(s).route(_BEIJING, _SHANGHAI, mode)
    assert (route.distance_m, route.duration_s, route.provider) == (12000, 1800, "amap")


@respx.mock
async def test_amap_route_transit_uses_adcode(settings: Settings) -> None:
    s = settings.model_copy(update={"amap_api_key": SecretStr("k")})
    regeo_route = respx.get(f"{_AMAP_BASE}/geocode/regeo").respond(
        200,
        json={"status": "1", "regeocode": {"addressComponent": {"adcode": "110000"}}},
    )
    transit_route = respx.get(f"{_AMAP_BASE}/direction/transit/integrated").respond(
        200,
        json={"status": "1", "route": {"transits": [{"distance": "15000", "duration": "2400"}]}},
    )
    route = await AMapProvider(s).route(_BEIJING, _SHANGHAI, "transit")
    assert (route.distance_m, route.duration_s) == (15000, 2400)
    assert regeo_route.calls.last.request.url.params["location"] == "116.4,39.9"
    assert transit_route.calls.last.request.url.params["city"] == "110000"


# ---------------- OSM：地理编码 ----------------


@respx.mock
async def test_osm_geocode_parses(settings: Settings) -> None:
    route = respx.get(_NOMINATIM_URL).respond(
        200, json=[{"lat": "39.90", "lon": "116.39", "display_name": "Beijing"}]
    )
    provider = OsmMapsProvider(settings)
    assert provider.configured is True
    point = await provider.geocode("Beijing")
    assert point == GeoPoint(lat=39.9, lng=116.39)
    params = route.calls.last.request.url.params
    assert params["format"] == "jsonv2"
    assert params["accept-language"] == "zh"


@respx.mock
async def test_osm_geocode_empty_raises(settings: Settings) -> None:
    respx.get(_NOMINATIM_URL).respond(200, json=[])
    with pytest.raises(ProviderError, match="未能地理编码"):
        await OsmMapsProvider(settings).geocode("未知地")


# ---------------- OSM：POI ----------------


@respx.mock
async def test_osm_search_poi_node_and_way(settings: Settings) -> None:
    route = respx.post(_OVERPASS_URL).respond(
        200,
        json={
            "elements": [
                {
                    "type": "node",
                    "id": 1,
                    "lat": 39.90,
                    "lon": 116.39,
                    "tags": {"name": "咖啡馆", "amenity": "cafe", "opening_hours": "Mo-Fr 08:00"},
                },
                {
                    "type": "way",
                    "id": 2,
                    "center": {"lat": 39.91, "lon": 116.40},
                    "tags": {"name": "公园", "leisure": "park"},
                },
                {"type": "node", "id": 3, "lat": 39.0, "lon": 116.0, "tags": {}},
            ]
        },
    )
    respx.get(_NOMINATIM_URL).respond(200, json=[{"lat": "39.9", "lon": "116.4"}])
    pois = await OsmMapsProvider(settings).search_poi("Beijing", "咖啡馆", None, 5)
    assert [poi.poi_id for poi in pois] == ["node/1", "way/2"]
    cafe, park = pois
    assert cafe.categories == ["cafe"]
    assert cafe.business_hours == "Mo-Fr 08:00"
    assert cafe.provider == "osm"
    assert str(cafe.sources[0]) == "https://www.openstreetmap.org/node/1"
    assert park.categories == []  # leisure 不在采集类目内
    form = parse_qs(route.calls.last.request.content.decode())
    query = form["data"][0]
    assert "around:8000,39.9,116.4" in query
    assert "咖啡馆" in query


# ---------------- OSM：路线 ----------------


@pytest.mark.parametrize(
    ("mode", "prefix"),
    [("driving", _OSRM_CAR), ("walking", _OSRM_FOOT)],
)
@respx.mock
async def test_osm_route_driving_walking(settings: Settings, mode: TravelMode, prefix: str) -> None:
    respx.get(url__startswith=prefix).respond(
        200, json={"code": "Ok", "routes": [{"distance": 15000.0, "duration": 3000.0}]}
    )
    route = await OsmMapsProvider(settings).route(_BEIJING, _SHANGHAI, mode)
    assert (route.distance_m, route.duration_s, route.provider) == (15000, 3000, "osm")


@respx.mock
async def test_osm_route_failure_code(settings: Settings) -> None:
    respx.get(url__startswith=_OSRM_CAR).respond(200, json={"code": "Invalid"})
    with pytest.raises(ProviderError, match="OSRM"):
        await OsmMapsProvider(settings).route(_BEIJING, _SHANGHAI, "driving")


async def test_osm_transit_unsupported_without_http(settings: Settings) -> None:
    # 未注册任何 respx 路由：一旦发出请求即失败
    with pytest.raises(UnsupportedTravelMode, match="公交"):
        await OsmMapsProvider(settings).route(_BEIJING, _SHANGHAI, "transit")


@respx.mock
async def test_osm_geocoder_http_error(settings: Settings) -> None:
    respx.get(_NOMINATIM_URL).respond(500)
    # Provider 不吞传输层错误，交由 run_chain 重试/降级
    with pytest.raises(httpx.HTTPStatusError):
        await OsmMapsProvider(settings).geocode("no-mock")
