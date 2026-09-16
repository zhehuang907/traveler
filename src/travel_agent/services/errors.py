"""能力层异常体系。

services 不依赖 api 层，因此这里自定义异常；
后续阶段在接口层统一映射为错误信封中的 ErrorCode。
"""

from collections.abc import Sequence


class ProviderError(RuntimeError):
    """单个 Provider 调用失败的基类（网络、限流、协议解析等）。"""


class ProviderConfigError(ProviderError):
    """Provider 缺少必要配置（如 Key 为空），降级链应直接跳过。"""


class OutsideForecastWindow(ProviderError):
    """请求日期超出该 Provider 的预报窗口，服务层改走气候参考。"""


class UnsupportedTravelMode(ProviderError):
    """该 Provider 不支持请求的出行方式（如 OSM 无公交路径）。"""


class AllProvidersFailed(RuntimeError):
    """降级链上所有 Provider 都失败（或全部未配置）。"""

    def __init__(self, capability: str, causes: Sequence[tuple[str, str]] = ()) -> None:
        self.capability = capability
        self.causes = list(causes)
        detail = "; ".join(f"{name}: {err}" for name, err in self.causes) or "无可用 Provider"
        super().__init__(f"能力 {capability} 的全部 Provider 均失败 -> {detail}")
