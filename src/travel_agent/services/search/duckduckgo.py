"""DuckDuckGo 免 Key 兜底 Provider（duckduckgo-search 包，同步调用放到线程）。"""

import asyncio
from collections.abc import Callable
from typing import Protocol, cast

from pydantic import ValidationError

from travel_agent.config import Settings
from travel_agent.domain.models import SearchResult
from travel_agent.services.errors import ProviderError
from travel_agent.services.search.base import SearchTimeRange

# 统一时间窗 -> DDG timelimit
_TIMELIMIT_MAP: dict[str, str] = {"day": "d", "week": "w", "month": "m", "year": "y"}


class DDGSClient(Protocol):
    """duckduckgo_search.DDGS 实际用到的最小接口（便于注入测试替身）。"""

    def text(
        self,
        keywords: str,
        region: str | None = ...,
        safesearch: str = ...,
        timelimit: str | None = ...,
        backend: str = ...,
        max_results: int | None = ...,
    ) -> list[dict[str, str]]:
        """文本搜索。"""
        ...


class DuckDuckGoSearchProvider:
    """免 Key，永远 configured；依赖缺失/被安全策略拦截时在调用期报错。"""

    name = "duckduckgo"

    def __init__(
        self,
        settings: Settings,
        ddgs_factory: Callable[[], DDGSClient] | None = None,
    ) -> None:
        self._settings = settings
        self._ddgs_factory = ddgs_factory

    @property
    def configured(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        max_results: int,
        time_range: SearchTimeRange | None,
    ) -> list[SearchResult]:
        return await asyncio.to_thread(self._search_sync, query, max_results, time_range)

    def _search_sync(
        self,
        query: str,
        max_results: int,
        time_range: SearchTimeRange | None,
    ) -> list[SearchResult]:
        client = self._build_client()
        raw_items = client.text(
            query,
            region="wt-wt",
            safesearch="moderate",
            timelimit=_TIMELIMIT_MAP.get(time_range) if time_range else None,
            max_results=max_results,
        )
        return [item for item in (_to_result(raw) for raw in raw_items) if item is not None]

    def _build_client(self) -> DDGSClient:
        if self._ddgs_factory is not None:
            return self._ddgs_factory()
        try:
            from duckduckgo_search import DDGS
        except ImportError as exc:  # 本机策略拦截/未安装走降级链失败统计
            raise ProviderError("duckduckgo-search 依赖不可用") from exc
        return cast(DDGSClient, DDGS())


def _to_result(raw: dict[str, str]) -> SearchResult | None:
    try:
        return SearchResult(
            title=raw.get("title", ""),
            url=raw.get("href", ""),
            snippet=raw.get("body", ""),
            provider="duckduckgo",
        )
    except ValidationError:
        return None
