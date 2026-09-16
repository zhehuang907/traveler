"""MapsService 集成测试：降级链、TTL 缓存、参数校验（respx mock）。"""

import pytest
import respx
from pydantic import SecretStr

from travel_agent.config import Settings
from travel_agent.domain.models import GeoPoint
from travel_agent.services.maps.service import MapsService

_AMAP_GEO = "https://restapi.amap.com/v3/geocode/geo"
_AMAP_PLACE = "https://restapi.amap.com/v3/place/text"
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OSRM_CAR_PREFIX = "https://routing.openstreetmap.de/routed-car/route/v1/driving/"

# 默认链仅高德（OSM 公共端点在目标部署环境不可达）；OSM 兜底用例显式开启
_WITH_OSM = {"maps_provider_chain": ["amap", "osm"]}


@respx.mock
async def test_service_falls_back_to_osm_when_amap_unconfigured(settings: Settings) -> None:
    # 不注册高德路由：一旦尝试高德（未配置会被跳过，不应发请求）即由 respx 报错
    route = respx.get(_NOMINATIM_URL).respond(200, json=[{"lat": "39.90", "lon": "116.39"}])
    service = MapsService(settings.model_copy(update=_WITH_OSM))
    point = await service.geocode("北京")
    assert point == GeoPoint(lat=39.9, lng=116.39)
    assert route.call_count == 1


@respx.mock
async def test_service_geocode_cache_second_call_no_http(settings: Settings) -> None:
    route = respx.get(_NOMINATIM_URL).respond(200, json=[{"lat": "39.9", "lon": "116.4"}])
    service = MapsService(settings.model_copy(update=_WITH_OSM))
    first = await service.geocode("Beijing")
    second = await service.geocode("Beijing")
    assert first == second
    assert route.call_count == 1


def test_default_chain_is_amap_only(settings: Settings) -> None:
    # 默认链仅高德：未配置 Key 时无任何已配置 Provider（OSM 不可达，不进默认链）
    providers = MapsService(settings)._providers  # 测试需要直接检查装配结果
    assert [provider.name for provider in providers] == ["amap"]
    assert [provider.configured for provider in providers] == [False]


def test_cached_provider_forwards_name_and_configured(settings: Settings) -> None:
    service = MapsService(settings.model_copy(update=_WITH_OSM))
    providers = service._providers  # 测试需要直接检查装配结果
    names = [provider.name for provider in providers]
    assert names == ["amap", "osm"]
    # 无 Key：高德未配置，OSM 恒配置
    configured = [provider.configured for provider in providers]
    assert configured == [False, True]


@respx.mock
async def test_service_uses_amap_first_when_configured(settings: Settings) -> None:
    s = settings.model_copy(update={"amap_api_key": SecretStr("amap-key")})
    route = respx.get(_AMAP_GEO).respond(
        200, json={"status": "1", "geocodes": [{"location": "121.47,31.23"}]}
    )
    # 不注册 Nominatim：命中高德后不应触达 OSM
    point = await MapsService(s).geocode("上海")
    assert point == GeoPoint(lat=31.23, lng=121.47)
    assert route.calls.last.request.url.params["key"] == "amap-key"


@respx.mock
async def test_service_poi_limit_clamped(settings: Settings) -> None:
    s = settings.model_copy(update={"amap_api_key": SecretStr("k")})
    route = respx.get(_AMAP_PLACE).respond(200, json={"status": "1", "pois": []})
    service = MapsService(s)
    await service.search_poi("北京", "故宫", limit=0)
    assert route.calls.last.request.url.params["offset"] == "1"
    await service.search_poi("北京", "故宫", limit=99)
    assert route.calls.last.request.url.params["offset"] == "20"


@pytest.mark.parametrize(
    ("city", "keywords"),
    [("", "x"), ("  ", "x"), ("北京", ""), ("北京", "字" * 61)],
)
async def test_service_validates_text(settings: Settings, city: str, keywords: str) -> None:
    with pytest.raises(ValueError):
        await MapsService(settings).search_poi(city, keywords)


async def test_service_geocode_blank_address(settings: Settings) -> None:
    with pytest.raises(ValueError):
        await MapsService(settings).geocode("  ")


@respx.mock
async def test_service_route_falls_back_to_osrm(settings: Settings) -> None:
    route = respx.get(url__startswith=_OSRM_CAR_PREFIX).respond(
        200, json={"code": "Ok", "routes": [{"distance": 120_000.0, "duration": 7_200.0}]}
    )
    origin = GeoPoint(lat=39.9, lng=116.4)
    destination = GeoPoint(lat=31.2, lng=121.5)
    info = await MapsService(settings.model_copy(update=_WITH_OSM)).route(
        origin, destination, "driving"
    )
    assert (info.distance_m, info.duration_s, info.provider) == (120_000, 7_200, "osm")
    requested = route.calls.last.request.url.path
    assert requested == "/routed-car/route/v1/driving/116.4,39.9;121.5,31.2"
