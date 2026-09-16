"""diskcache 本地 TTL 缓存（异步封装）。

- diskcache 是同步库，全部读写经 ``asyncio.to_thread`` 避免阻塞事件循环。
- 缓存键必须包含 provider 名与归一化参数，杜绝降级链不同源数据串用。
- 缓存自身故障不允许影响主流程：读写异常一律降级为直连并打 warning。
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import diskcache

from travel_agent.config import Settings
from travel_agent.logging_conf import get_logger

log = get_logger(component="cache")
T = TypeVar("T")


def stable_key(namespace: str, provider: str, **params: Any) -> str:
    """生成稳定缓存键：namespace + provider + 排序后的参数 JSON 哈希。"""
    encoded = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(f"{namespace}|{provider}|{encoded}".encode()).hexdigest()
    return f"{namespace}:{digest}"


class TTLCache:
    """按 settings.cache_dir 落盘的 TTL 缓存。"""

    def __init__(self, settings: Settings, *, subdir: str = "responses") -> None:
        self._dir = settings.cache_dir / subdir
        self._client: diskcache.Cache | None = None

    def _get_client(self) -> diskcache.Cache:
        if self._client is None:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._client = diskcache.Cache(str(self._dir))
        return self._client

    async def get(self, key: str) -> tuple[Any, bool]:
        """返回 (值, 是否命中)。任何缓存异常都视为未命中。"""
        try:
            value = await asyncio.to_thread(self._get_client().get, key, default=None)
        except Exception as exc:  # 缓存不可用不应影响业务主流程
            log.warning("cache_read_failed", key=key, error=f"{type(exc).__name__}: {exc}")
            return None, False
        return (value, True) if value is not None else (None, False)

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            # diskcache 基于 pickle，Pydantic v2 模型与列表均可直接序列化
            await asyncio.to_thread(self._get_client().set, key, value, expire=ttl_seconds)
        except Exception as exc:  # 写缓存失败降级为直连
            log.warning("cache_write_failed", key=key, error=f"{type(exc).__name__}: {exc}")

    async def get_or_set(
        self,
        key: str,
        ttl_seconds: int,
        factory: Callable[[], Awaitable[T]],
    ) -> tuple[T, bool]:
        """命中直接返回；未命中调用 factory 并回填。返回 (值, cache_hit)。"""
        cached, hit = await self.get(key)
        if hit:
            log.info("cache_hit", key=key)
            return cached, True
        value = await factory()
        await self.set(key, value, ttl_seconds)
        return value, False

    def clear(self) -> None:
        """清空缓存（测试与 doctor 修复用）。"""
        if self._client is not None:
            self._client.clear()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
