"""工具注册表：统一装配 services、信号量限流、1 QPS 节流、trace 与降级。

节点只与本注册表对话，不直接发 HTTP（Prompt 4 分层纪律）。
任何工具失败都不允许炸断图运行：``run`` 捕获异常、记 degraded trace、
返回 None，由节点决定如何降级（跳过/补提示）。
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import TypeVar

from travel_agent.agent.state import ToolTrace
from travel_agent.config import Settings
from travel_agent.logging_conf import get_logger
from travel_agent.services.cache import TTLCache
from travel_agent.services.maps.service import MapsService
from travel_agent.services.protocols import MapsBackend, SearchBackend, WeatherBackend
from travel_agent.services.search.service import SearchService
from travel_agent.services.weather.service import WeatherService

log = get_logger(component="tool-registry")
R = TypeVar("R")


class IntervalLimiter:
    """两次临界区放行之间至少间隔 min_interval 秒（POI/地理编码 1 QPS 节流）。"""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    def throttle(self) -> AbstractAsyncContextManager[None]:
        return _Throttle(self)

    async def _acquire(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = asyncio.get_running_loop().time()
            wait = self._last + self._min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = asyncio.get_running_loop().time()


class _Throttle(AbstractAsyncContextManager[None]):
    def __init__(self, limiter: IntervalLimiter) -> None:
        self._limiter = limiter

    async def __aenter__(self) -> None:
        await self._limiter._acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


class ToolRegistry:
    """全部 Agent 工具的唯一入口（含 services 句柄与运行期统计）。"""

    def __init__(
        self,
        settings: Settings,
        search: SearchBackend | None = None,
        maps: MapsBackend | None = None,
        weather: WeatherBackend | None = None,
        cache: TTLCache | None = None,
    ) -> None:
        self._settings = settings
        self._cache = cache
        self.search: SearchBackend = search or SearchService(settings, cache=self._cache)
        self.maps: MapsBackend = maps or MapsService(settings, cache=self._cache)
        self.weather: WeatherBackend = weather or WeatherService(settings, cache=self._cache)
        self._semaphore = asyncio.Semaphore(settings.fanout_concurrency)
        self.poi_limiter = IntervalLimiter(settings.poi_min_interval_s)
        self.traces: list[ToolTrace] = []

    async def run(self, tool: str, operation: Callable[[], Awaitable[R]]) -> R | None:
        """信号量内执行一次工具调用；失败降级为 None 并留痕。"""
        async with self._semaphore:
            try:
                result = await operation()
            except Exception as exc:  # 工具失败不炸断图：节点按 None 降级
                detail = f"{type(exc).__name__}: {exc}"
                log.warning("tool_degraded", tool=tool, error=detail)
                self.traces.append(ToolTrace(tool=tool, ok=False, degraded=True, detail=detail))
                return None
        self.traces.append(ToolTrace(tool=tool, ok=True, detail=_summarize(result)))
        return result

    def trace_snapshot(self) -> list[ToolTrace]:
        return [trace.model_copy(deep=True) for trace in self.traces]


def _summarize(result: object) -> str:
    if isinstance(result, list):
        return f"{len(result)} 条结果"
    if result is None:
        return "空结果"
    return type(result).__name__
