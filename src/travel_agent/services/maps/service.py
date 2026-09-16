"""地图能力服务：高德/OSM 降级链 + 分方法 TTL（地理编码/POI 24h，路线 7d）。"""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from travel_agent.config import Settings
from travel_agent.domain.models import GeoPoint, Poi, RouteInfo, TravelMode
from travel_agent.logging_conf import get_logger
from travel_agent.services.base import run_chain
from travel_agent.services.cache import TTLCache, stable_key
from travel_agent.services.maps.amap import AMapProvider
from travel_agent.services.maps.base import MapsProvider
from travel_agent.services.maps.osm import OsmMapsProvider
from travel_agent.services.retry import with_retry

log = get_logger(component="maps-service")
T = TypeVar("T")
_MAX_KEYWORD_LEN = 60
_MAX_POI_LIMIT = 20


class CachedMapsProvider:
    """给任意 MapsProvider 套上「按 Provider+参数」分键的 TTL 缓存。"""

    def __init__(
        self,
        inner: MapsProvider,
        settings: Settings,
        cache: TTLCache,
    ) -> None:
        self._inner = inner
        self._settings = settings
        self._cache = cache

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def configured(self) -> bool:
        return self._inner.configured

    async def geocode(self, address: str) -> GeoPoint:
        async def fetch() -> GeoPoint:
            return await self._inner.geocode(address)

        return await self._cached(
            "geocode",
            self._settings.cache_ttl_poi,
            {"address": address},
            fetch,
        )

    async def search_poi(
        self,
        city: str,
        keywords: str,
        category: str | None,
        limit: int,
    ) -> list[Poi]:
        async def fetch() -> list[Poi]:
            return await self._inner.search_poi(city, keywords, category, limit)

        return await self._cached(
            "poi",
            self._settings.cache_ttl_poi,
            {"city": city, "kw": keywords, "cat": category or "", "n": limit},
            fetch,
        )

    async def route(self, origin: GeoPoint, destination: GeoPoint, mode: TravelMode) -> RouteInfo:
        async def fetch() -> RouteInfo:
            return await self._inner.route(origin, destination, mode)

        return await self._cached(
            "route",
            self._settings.cache_ttl_route,
            {"o": origin.osm_coord(), "d": destination.osm_coord(), "m": mode},
            fetch,
        )

    async def _cached(
        self,
        namespace: str,
        ttl: int,
        params: dict[str, object],
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        key = stable_key(namespace, provider=self.name, **params)

        async def call() -> T:
            return await with_retry(
                operation,
                attempts=self._settings.external_retry_attempts,
                base_delay=self._settings.external_retry_base_delay,
            )

        value, _hit = await self._cache.get_or_set(key, ttl, call)
        return value


class MapsService:
    """对外唯一地图入口。"""

    def __init__(self, settings: Settings, cache: TTLCache | None = None) -> None:
        self._settings = settings
        self._cache = cache or TTLCache(settings)
        self._providers = self._build_providers(settings.maps_provider_chain)

    def _build_providers(self, chain: list[str]) -> list[MapsProvider]:
        registry: dict[str, Callable[[], MapsProvider]] = {
            "amap": lambda: AMapProvider(self._settings),
            "osm": lambda: OsmMapsProvider(self._settings),
        }
        providers: list[MapsProvider] = []
        for name in chain:
            factory = registry.get(name)
            if factory is None:
                log.warning("unknown_maps_provider_ignored", provider=name)
                continue
            providers.append(CachedMapsProvider(factory(), self._settings, self._cache))
        return providers

    async def geocode(self, address: str) -> GeoPoint:
        normalized = _require_text(address, "address")
        return await run_chain(
            "geocode",
            self._providers,
            lambda p: p.geocode(normalized),
            self._settings,
        )

    async def search_poi(
        self,
        city: str,
        keywords: str,
        category: str | None = None,
        limit: int = 10,
    ) -> list[Poi]:
        normalized_city = _require_text(city, "city")
        normalized_kw = _require_text(keywords, "keywords")
        capped = min(max(1, limit), _MAX_POI_LIMIT)
        return await run_chain(
            "poi_search",
            self._providers,
            lambda p: p.search_poi(normalized_city, normalized_kw, category, capped),
            self._settings,
        )

    async def route(
        self, origin: GeoPoint, destination: GeoPoint, mode: TravelMode = "driving"
    ) -> RouteInfo:
        return await run_chain(
            "route",
            self._providers,
            lambda p: p.route(origin, destination, mode),
            self._settings,
        )


def _require_text(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_KEYWORD_LEN:
        raise ValueError(f"{field} 长度必须在 1..{_MAX_KEYWORD_LEN} 之间")
    return normalized
