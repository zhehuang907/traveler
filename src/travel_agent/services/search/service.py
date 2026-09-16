"""搜索能力服务：按 SEARCH_PROVIDER_CHAIN 装配降级链 + TTL 缓存（6h）。"""

from collections.abc import Callable

from travel_agent.config import Settings
from travel_agent.domain.models import SearchResult
from travel_agent.logging_conf import get_logger
from travel_agent.services.base import run_chain
from travel_agent.services.cache import TTLCache, stable_key
from travel_agent.services.search.base import SearchProvider, SearchTimeRange
from travel_agent.services.search.bocha import BochaSearchProvider
from travel_agent.services.search.duckduckgo import DuckDuckGoSearchProvider
from travel_agent.services.search.tavily import TavilySearchProvider

log = get_logger(component="search-service")

_MAX_QUERY_LEN = 200
_MAX_RESULTS = 10


class SearchService:
    """对外唯一搜索入口；Provider 细节与降级顺序对上层透明。"""

    def __init__(self, settings: Settings, cache: TTLCache | None = None) -> None:
        self._settings = settings
        self._cache = cache or TTLCache(settings)
        self._providers = self._build_providers(settings.search_provider_chain)

    def _build_providers(self, chain: list[str]) -> list[SearchProvider]:
        registry: dict[str, Callable[[], SearchProvider]] = {
            "tavily": lambda: TavilySearchProvider(self._settings),
            "bocha": lambda: BochaSearchProvider(self._settings),
            "duckduckgo": lambda: DuckDuckGoSearchProvider(self._settings),
        }
        providers: list[SearchProvider] = []
        for name in chain:
            factory = registry.get(name)
            if factory is None:
                log.warning("unknown_search_provider_ignored", provider=name)
                continue
            providers.append(factory())
        return providers

    async def search(
        self,
        query: str,
        max_results: int = 5,
        time_range: SearchTimeRange | None = "year",
    ) -> list[SearchResult]:
        """搜索网页；先查缓存，未命中走降级链，结果整体缓存。"""
        normalized = query.strip()
        if not normalized or len(normalized) > _MAX_QUERY_LEN:
            raise ValueError(f"query 长度必须在 1..{_MAX_QUERY_LEN} 之间")
        capped = min(max(1, max_results), _MAX_RESULTS)
        key = stable_key(
            "web_search",
            provider=">".join(self._settings.search_provider_chain),
            q=normalized,
            n=capped,
            r=time_range,
        )

        async def fetch() -> list[SearchResult]:
            return await run_chain(
                "web_search",
                self._providers,
                lambda p: p.search(normalized, capped, time_range),
                self._settings,
            )

        results, _hit = await self._cache.get_or_set(key, self._settings.cache_ttl_search, fetch)
        return results
