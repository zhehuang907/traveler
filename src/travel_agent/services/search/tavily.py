"""Tavily 搜索 Provider（专为 LLM 设计，返回清洗摘要）。"""

from pydantic import ValidationError

from travel_agent.config import Settings
from travel_agent.domain.models import SearchResult
from travel_agent.services.http_client import build_client
from travel_agent.services.search.base import SearchTimeRange, parse_date

_TAVILY_URL = "https://api.tavily.com/search"


class TavilySearchProvider:
    """POST /search，Bearer 鉴权。"""

    name = "tavily"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.tavily_configured

    async def search(
        self,
        query: str,
        max_results: int,
        time_range: SearchTimeRange | None,
    ) -> list[SearchResult]:
        payload: dict[str, object] = {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
        }
        if time_range is not None:
            payload["time_range"] = time_range
        headers = {"Authorization": f"Bearer {self._settings.tavily_api_key.get_secret_value()}"}
        async with build_client(self._settings) as client:
            response = await client.post(_TAVILY_URL, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        return _parse(body)


def _parse(body: dict[str, object]) -> list[SearchResult]:
    raw_items = body.get("results", [])
    if not isinstance(raw_items, list):
        return []
    results: list[SearchResult] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            results.append(
                SearchResult(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("content", "")),
                    published_at=parse_date(item.get("published_date")),
                    provider="tavily",
                )
            )
        except ValidationError:
            # 单条脏数据不影响其余结果（URL 缺失/非法时跳过）
            continue
    return results
