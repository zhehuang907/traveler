"""Provider 抽象与优先级降级链。

每个能力（搜索/天气/地图）的 Provider 实现本模块 Protocol，
服务层按配置顺序逐个尝试，单个 Provider 内部先重试、链间再降级。
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from travel_agent.config import Settings
from travel_agent.logging_conf import get_logger
from travel_agent.services.errors import AllProvidersFailed
from travel_agent.services.retry import with_retry

log = get_logger(component="provider-chain")


class Provider(Protocol):
    """能力 Provider 的最小契约。"""

    @property
    def name(self) -> str:
        """Provider 标识（与配置链中的名称一致）。"""
        ...

    @property
    def configured(self) -> bool:
        """Key/依赖是否就绪；False 时降级链直接跳过。"""
        ...


async def run_chain[R, P: Provider](
    capability: str,
    providers: Sequence[P],
    operation: Callable[[P], Awaitable[R]],
    settings: Settings,
) -> R:
    """按顺序执行降级链：未配置跳过、瞬时错误重试、最终失败切下一个。"""
    causes: list[tuple[str, str]] = []
    for provider in providers:
        if not provider.configured:
            log.debug("provider_skipped", capability=capability, provider=provider.name)
            continue

        async def attempt(current: P = provider) -> R:
            return await operation(current)

        try:
            result = await with_retry(
                attempt,
                attempts=settings.external_retry_attempts,
                base_delay=settings.external_retry_base_delay,
            )
        except Exception as exc:  # 降级链必须兜住单家故障再切下一家
            detail = f"{type(exc).__name__}: {exc}"
            log.warning(
                "provider_failed",
                capability=capability,
                provider=provider.name,
                error=detail,
            )
            causes.append((provider.name, detail))
            continue
        log.info("provider_hit", capability=capability, provider=provider.name)
        return result
    raise AllProvidersFailed(capability, causes)
