"""能力层：对外部世界的封装（搜索/天气/地图/缓存/PDF）。

services 不感知 LLM 与 Agent 编排，只负责把外部响应转成 domain 模型。
"""

from travel_agent.services.cache import TTLCache, stable_key
from travel_agent.services.errors import (
    AllProvidersFailed,
    OutsideForecastWindow,
    ProviderConfigError,
    ProviderError,
    UnsupportedTravelMode,
)

__all__ = [
    "AllProvidersFailed",
    "OutsideForecastWindow",
    "ProviderConfigError",
    "ProviderError",
    "TTLCache",
    "UnsupportedTravelMode",
    "stable_key",
]
