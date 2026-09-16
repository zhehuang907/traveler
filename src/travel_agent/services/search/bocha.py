"""博查 Bocha 搜索 Provider（国内，返回网页摘要）。"""

from pydantic import ValidationError

from travel_agent.config import Settings
from travel_agent.domain.models import SearchResult
from travel_agent.services.http_client import build_client
from travel_agent.services.search.base import SearchTimeRange, parse_date

_BOCHA_URL = "https://api.bochaai.com/v1/web-search"
# 统一时间窗 -> Bocha freshness 取值
_FRESHNESS_MAP = {
    "day": "oneDay",
    "week": "oneWeek",
    "month": "oneMonth",
    "year": "oneYear",
}


class BochaSearchProvider:
    """POST /v1/web-search，Bearer 鉴权。"""

    name = "bocha"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.bocha_configured

    async def search(
        self,
        query: str,
        max_results: int,
        time_range: SearchTimeRange | None,
    ) -> list[SearchResult]:
        payload: dict[str, object] = {"query": query, "count": max_results}
        if time_range is not None:
            payload["freshness"] = _FRESHNESS_MAP[time_range]
        headers = {"Authorization": f"Bearer {self._settings.bocha_api_key.get_secret_value()}"}
        async with build_client(self._settings) as client:
            response = await client.post(_BOCHA_URL, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        return _parse(body)


def _parse(body: dict[str, object]) -> list[SearchResult]:
    data = body.get("data")
    if not isinstance(data, dict):
        return []
    web_pages = data.get("webPages")
    if not isinstance(web_pages, dict):
        return []
    raw_items = web_pages.get("value", [])
    if not isinstance(raw_items, list):
        return []
    results: list[SearchResult] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            results.append(
                SearchResult(
                    title=str(item.get("name", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("snippet", "")),
                    published_at=parse_date(item.get("datePublished")),
                    provider="bocha",
                )
            )
        except ValidationError:
            continue
    return results
