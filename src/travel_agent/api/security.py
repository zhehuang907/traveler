"""登录/注册防爆破：单进程内存限流（符合 ADR-007 单 worker 约束）。

策略：
- 按「用户名」与「客户端 IP」双键独立计数（互不干扰）；
- 窗口内失败次数达到阈值 → 锁定该键一段时间，锁定期间直接 429；
- 登录成功清除对应 username / IP 计数；
- 进程重启即清零：单进程部署下可接受，跨重启持久化属 DB 方案范畴。

实现是纯内存、无 IO，FastAPI 单事件循环下字典读写原子；仍加互斥锁
以兼容未来多线程场景（如 uvicorn --workers 前的过渡）。
"""

from __future__ import annotations

from threading import Lock
from time import monotonic

from travel_agent.api.errors import AppError, ErrorCode


class LoginThrottle:
    """按 key（用户名 / IP / 注册 IP）累计失败并锁定。"""

    def __init__(self, *, max_failures: int, lock_seconds: int) -> None:
        self._max_failures = max_failures
        self._window_seconds = lock_seconds
        self._lock_seconds = lock_seconds
        self._failures: dict[str, list[float]] = {}
        self._locks: dict[str, float] = {}
        self._guard = Lock()

    def is_locked(self, key: str) -> bool:
        """当前是否处于锁定状态。"""
        with self._guard:
            return self._locks.get(key, 0.0) > monotonic()

    def check(self, key: str) -> None:
        """命中锁定则抛 429；否则静默通过（不消耗配额）。"""
        if self.is_locked(key):
            raise AppError(
                ErrorCode.RATE_LIMITED,
                "尝试次数过多，请稍后再试",
                status_code=429,
            )

    def record_failure(self, key: str) -> None:
        """记录一次失败；窗口内累计达到阈值则进入锁定。"""
        now = monotonic()
        with self._guard:
            if self._locks.get(key, 0.0) > now:
                return
            stamps = [t for t in self._failures.get(key, []) if now - t < self._window_seconds]
            stamps.append(now)
            self._failures[key] = stamps
            if len(stamps) >= self._max_failures:
                self._locks[key] = now + self._lock_seconds
                self._failures.pop(key, None)

    def clear(self, key: str) -> None:
        """成功后清除该键的失败记录与锁定。"""
        with self._guard:
            self._failures.pop(key, None)
            self._locks.pop(key, None)
