"""通用网页搜索：Provider 协议与共用解析工具。"""

from datetime import date, datetime
from typing import Literal, Protocol

from travel_agent.domain.models import SearchResult

# 与 Tavily time_range 对齐的统一时间窗取值
SearchTimeRange = Literal["day", "week", "month", "year"]


class SearchProvider(Protocol):
    """搜索 Provider 契约。"""

    @property
    def name(self) -> str:
        """Provider 标识（tavily/bocha/duckduckgo）。"""
        ...

    @property
    def configured(self) -> bool:
        """Key 或本地依赖是否就绪。"""
        ...

    async def search(
        self,
        query: str,
        max_results: int,
        time_range: SearchTimeRange | None,
    ) -> list[SearchResult]:
        """执行搜索并返回归一化结果。"""
        ...


def parse_date(value: object) -> date | None:
    """各家发布时间格式宽松解析（``2024-05-01`` 或 ISO 时间串），失败返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None
