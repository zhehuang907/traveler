"""酒店能力 Provider 协议。

拆成两个正交的能力，便于按数据可得性组合：

- :class:`HotelSearchProvider` —— 「有哪些酒店」。必须能给出真实存在的酒店，
  这是硬需求，拿不到就该报错而不是编造。
- :class:`HotelPriceProvider` —— 「多少钱」。**允许返回 None/空**：取不到就如实
  告知，由服务层降级为参考价或「暂无报价」。绝不允许用估算值填充。
"""

from datetime import date
from typing import Protocol

from travel_agent.domain.models import GeoPoint, Hotel, HotelTier, RoomType


class HotelSearchProvider(Protocol):
    """酒店检索能力。"""

    @property
    def name(self) -> str:
        """Provider 标识（amap）。"""
        ...

    @property
    def configured(self) -> bool:
        """Key 是否就绪。"""
        ...

    async def search_hotels(
        self,
        city: str,
        *,
        near: GeoPoint | None,
        keyword: str,
        limit: int,
    ) -> list[Hotel]:
        """检索酒店。``near`` 非空时优先按距离排序。

        返回的 ``Hotel.prices`` 可为空——本能力只保证「酒店真实存在」。
        """
        ...

    async def search_by_tier(
        self,
        city: str,
        *,
        near: GeoPoint | None,
        tier: HotelTier,
        limit: int,
    ) -> list[Hotel]:
        """按档次检索酒店。``Hotel.tier`` 未能判定的项不应返回。

        该方法为**可选扩展**：不支持分档的 Provider 可不实现，
        服务层会退化为「平铺检索 + 按 ``Hotel.tier`` 客户端分组」。
        """
        ...


class HotelPriceProvider(Protocol):
    """酒店房价能力（可选）。"""

    @property
    def name(self) -> str: ...

    @property
    def configured(self) -> bool: ...

    async def quote(
        self,
        hotel_name: str,
        city: str,
        check_in: date,
        check_out: date,
        travelers: int,
    ) -> list[tuple[RoomType, float, str]]:
        """查询指定日期区间的每晚报价。

        Returns:
            ``(房型, 每晚价, 备注)`` 三元组列表；**取不到价格时返回空列表**，
            不抛异常、不返回估算值。服务层据此把 ``price_confidence``
            标为 ``unavailable``。
        """
        ...
