"""tenacity 指数退避重试策略（全能力层统一）。

仅对瞬时错误重试：网络层异常、408/429/5xx；
鉴权失败、参数错误等 4xx 立即抛出，避免无意义重试烧额度。
"""

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from travel_agent.logging_conf import get_logger

log = get_logger(component="retry")

_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def is_retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUSES
    # TimeoutException 是 TransportError 子类；协议错误/本地解析错误也属瞬时网络面
    return isinstance(exc, httpx.TransportError)


def _log_sleep(retry_state: Any) -> None:
    log.warning(
        "provider_retry",
        attempt=retry_state.attempt_number,
        error=str(retry_state.outcome.exception() if retry_state.outcome else "?"),
    )


async def with_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_delay: float,
) -> T:
    """执行异步 operation，瞬时错误按指数退避重试，最终异常原样抛出。"""
    retrying = AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=base_delay, min=base_delay, max=base_delay * 8),
        retry=retry_if_exception(is_retryable),
        reraise=True,
        before_sleep=_log_sleep,
    )
    return await retrying(operation)
