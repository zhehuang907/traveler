"""tenacity 重试判定与退避执行测试。"""

import httpx
import pytest

from travel_agent.services.retry import is_retryable, with_retry


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.com/api")
    return httpx.HTTPStatusError(
        f"HTTP {code}", request=request, response=httpx.Response(code, request=request)
    )


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_retryable_status_codes(code: int) -> None:
    assert is_retryable(_status_error(code)) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_non_retryable_status_codes(code: int) -> None:
    assert is_retryable(_status_error(code)) is False


def test_transport_errors_retryable() -> None:
    assert is_retryable(httpx.ConnectError("connection refused")) is True
    assert is_retryable(httpx.TimeoutException("timed out")) is True


def test_business_errors_not_retryable() -> None:
    assert is_retryable(ValueError("bad payload")) is False


async def test_retry_recovers_after_transient_429() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _status_error(429)
        return "ok"

    result = await with_retry(operation, attempts=3, base_delay=0)
    assert result == "ok"
    assert calls == 3


async def test_non_retryable_error_fails_fast() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise _status_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(operation, attempts=3, base_delay=0)
    assert calls == 1


async def test_exhausted_attempts_reraises() -> None:
    calls = 0

    async def operation() -> str:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        await with_retry(operation, attempts=2, base_delay=0)
    assert calls == 2
