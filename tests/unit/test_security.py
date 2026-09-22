"""LoginThrottle 单元测试：计数、锁定、清除、键隔离。"""

import pytest

from travel_agent.api.errors import AppError, ErrorCode
from travel_agent.api.security import LoginThrottle


def test_throttle_allows_below_threshold() -> None:
    throttle = LoginThrottle(max_failures=3, lock_seconds=60)
    throttle.record_failure("k")
    throttle.record_failure("k")
    throttle.check("k")  # 未达阈值不抛


def test_throttle_locks_at_threshold() -> None:
    throttle = LoginThrottle(max_failures=3, lock_seconds=60)
    for _ in range(3):
        throttle.record_failure("k")
    with pytest.raises(AppError) as excinfo:
        throttle.check("k")
    assert excinfo.value.status_code == 429
    assert excinfo.value.code is ErrorCode.RATE_LIMITED


def test_throttle_clear_resets() -> None:
    throttle = LoginThrottle(max_failures=3, lock_seconds=60)
    for _ in range(3):
        throttle.record_failure("k")
    throttle.clear("k")
    throttle.check("k")  # 清除后不再抛


def test_throttle_keys_independent() -> None:
    throttle = LoginThrottle(max_failures=2, lock_seconds=60)
    throttle.record_failure("a")
    throttle.record_failure("a")
    with pytest.raises(AppError):
        throttle.check("a")
    throttle.check("b")  # 其他键不受影响


def test_throttle_locked_failure_is_idempotent() -> None:
    """锁定期间继续 record_failure 不改变锁定状态、不重复累计。"""
    throttle = LoginThrottle(max_failures=2, lock_seconds=60)
    throttle.record_failure("k")
    throttle.record_failure("k")
    throttle.record_failure("k")
    throttle.record_failure("k")
    with pytest.raises(AppError):
        throttle.check("k")
