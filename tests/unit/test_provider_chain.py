"""run_chain 降级链编排测试：跳过未配置、失败转移、全失败聚合原因。"""

import pytest

from travel_agent.config import Settings
from travel_agent.services.base import run_chain
from travel_agent.services.errors import AllProvidersFailed, ProviderError


class FakeProvider:
    """记录调用次数的测试替身。"""

    def __init__(
        self,
        name: str,
        *,
        configured: bool = True,
        outcome: str | Exception = "ok",
    ) -> None:
        self.name = name
        self._configured = configured
        self._outcome = outcome
        self.calls = 0

    @property
    def configured(self) -> bool:
        return self._configured

    async def call(self) -> str:
        self.calls += 1
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return f"{self.name}:{self._outcome}"


async def test_skips_unconfigured_and_hits_next(settings: Settings) -> None:
    off = FakeProvider("first", configured=False)
    on = FakeProvider("second")
    result = await run_chain("cap", [off, on], lambda p: p.call(), settings)
    assert result == "second:ok"
    assert off.calls == 0
    assert on.calls == 1


async def test_failure_falls_through_to_next_provider(settings: Settings) -> None:
    bad = FakeProvider("first", outcome=ProviderError("500"))
    good = FakeProvider("second")
    result = await run_chain("cap", [bad, good], lambda p: p.call(), settings)
    assert result == "second:ok"
    assert bad.calls == 1
    assert good.calls == 1


async def test_non_retryable_error_does_not_retry_same_provider(settings: Settings) -> None:
    bad = FakeProvider("first", outcome=ValueError("bad input"))
    good = FakeProvider("second")
    result = await run_chain("cap", [bad, good], lambda p: p.call(), settings)
    assert result == "second:ok"
    assert bad.calls == 1  # ValueError 不触发重试


async def test_all_failed_aggregates_causes(settings: Settings) -> None:
    first = FakeProvider("a", outcome=ProviderError("boom-a"))
    second = FakeProvider("b", outcome=ProviderError("boom-b"))
    with pytest.raises(AllProvidersFailed) as exc_info:
        await run_chain("weather", [first, second], lambda p: p.call(), settings)
    error = exc_info.value
    assert error.capability == "weather"
    assert [name for name, _detail in error.causes] == ["a", "b"]
    detail_text = str(error)
    assert "boom-a" in detail_text and "boom-b" in detail_text


async def test_empty_chain_raises_all_providers_failed(settings: Settings) -> None:
    with pytest.raises(AllProvidersFailed, match="无可用 Provider"):
        await run_chain("geo", [], lambda p: p.call(), settings)


async def test_first_success_short_circuits(settings: Settings) -> None:
    first = FakeProvider("a", outcome="fast")
    second = FakeProvider("b")
    result = await run_chain("cap", [first, second], lambda p: p.call(), settings)
    assert result == "a:fast"
    assert second.calls == 0
