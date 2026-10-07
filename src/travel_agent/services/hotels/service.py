"""酒店推荐服务：检索真实酒店 + 按人数推断房型 + 可选价格叠加。

价格诚实性原则（本模块最重要的约束）
--------------------------------------
市场里的真实房价来自 OTA 动态接口，需要签名且变动频繁。本服务按可得性分三档，
并如实标注，**任何情况下都不用估算值冒充报价**：

1. ``realtime``   —— 价格 Provider 按「酒店名+城市+入住日期」查到的当日报价
2. ``reference`` —— 来源方给的参考均价（高德 ``lowest_price``/``cost``），
   前端必须显示为「参考均价」，不能写「实时价」
3. ``unavailable``—— 确实没取到，``prices`` 为空，前端显示「暂无报价」

房型推断是**纯规则**（见 :mod:`travel_agent.domain.rooming`），不依赖价格——
即使完全拿不到价格，用户仍能得到「该订几间房、每间住几人」的结论。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import date
from typing import Final

from travel_agent.config import Settings
from travel_agent.domain.models import (
    HOTEL_TIER_META,
    GeoPoint,
    Hotel,
    HotelPrice,
    HotelRecommendation,
    HotelTier,
    RoomType,
    TieredHotelRecommendation,
    TierOption,
)
from travel_agent.domain.rooming import describe_suggestions, suggest_rooms
from travel_agent.logging_conf import get_logger
from travel_agent.services.base import run_chain
from travel_agent.services.cache import TTLCache, stable_key
from travel_agent.services.hotels.amap import AMapHotelProvider
from travel_agent.services.hotels.base import HotelPriceProvider, HotelSearchProvider
from travel_agent.services.retry import with_retry

log = get_logger(component="hotel-service")

#: 酒店检索结果缓存 TTL。酒店本身变化很慢，但价格变化快，
#: 故检索用长 TTL、价格叠加用短 TTL（见 ``_PRICE_TTL_SECONDS``）。
_SEARCH_TTL_FALLBACK = 21_600
#: 价格查询缓存：30 分钟。真实房价按小时波动，缓存过久会误导用户。
_PRICE_TTL_FALLBACK = 1_800
#: 单次推荐返回的酒店上限
_MAX_HOTELS = 12
#: 三档推荐的固定顺序（从省钱到享受），前端直接按序渲染
_TIER_ORDER: Final[tuple[HotelTier, ...]] = ("value", "comfort", "luxury")
#: 每档默认返回条数——用户要的是「帮我挑一家」，1 条最贴合决策场景
_MAX_PER_TIER = 3

_NO_REALTIME_DISCLAIMER = (
    "当前为参考均价，非指定日期的实时房价。实际价格以携程/美团/酒店官方渠道为准。"
)
_NO_PRICE_DISCLAIMER = "暂未获取到该酒店价格。建议前往携程、美团等平台按入住日期查询并比价。"


class HotelService:
    """对外唯一酒店推荐入口。"""

    def __init__(
        self,
        settings: Settings,
        cache: TTLCache | None = None,
        search_providers: list[HotelSearchProvider] | None = None,
        price_providers: list[HotelPriceProvider] | None = None,
    ) -> None:
        self._settings = settings
        self._cache = cache or TTLCache(settings, subdir="hotels")
        default_search = _default_search_providers(settings)
        self._search_providers: list[HotelSearchProvider] = (
            search_providers if search_providers is not None else default_search
        )
        self._price_providers: list[HotelPriceProvider] = (
            price_providers if price_providers is not None else _default_price_providers(settings)
        )

    @property
    def price_available(self) -> bool:
        """是否存在可用的价格 Provider（前端据此决定是否展示价格列）。"""
        return any(p.configured for p in self._price_providers)

    async def recommend(
        self,
        city: str,
        *,
        travelers: int,
        check_in: date,
        check_out: date,
        near: GeoPoint | None = None,
        keyword: str = "",
        limit: int = 8,
    ) -> HotelRecommendation:
        """给出酒店推荐。

        Args:
            city: 城市名，如「杭州」。
            travelers: 出行人数（含儿童），用于推断房型。
            check_in / check_out: 入住/离店日期，用于换算总价。
            near: 行程锚点坐标（通常是首个景点），用于按距离排序。
            keyword: 可选的偏好关键词，如「亲子」「近地铁」。
            limit: 返回条数上限。

        Raises:
            ValueError: 参数非法（日期倒置、人数超限）。
            AllProvidersFailed: 所有检索 Provider 都失败——此时不返回任何酒店，
                绝不用占位数据充数。
        """
        nights = _validate(check_in, check_out, travelers)
        capped = min(max(1, limit), _MAX_HOTELS)

        # 1) 房型推断（纯规则，先算——即使价格全拿不到，这部分也有价值）
        rooms = suggest_rooms(travelers)

        # 2) 检索真实酒店（走降级链 + TTL 缓存）
        hotels = await run_chain(
            "hotel_search",
            self._search_providers,
            lambda p: self._search_cached(p, city, near, keyword, capped),
            self._settings,
        )
        hotels = hotels[:capped]
        log.info(
            "hotel_search_done",
            city=city,
            travelers=travelers,
            nights=nights,
            found=len(hotels),
            room_plan=describe_suggestions(rooms),
        )

        # 3) 叠加价格（可失败、可为空，不影响主流程）
        if self.price_available:
            hotels = await self._attach_prices(hotels, city, check_in, check_out, travelers, nights)

        return HotelRecommendation(
            city=city,
            check_in=check_in,
            check_out=check_out,
            travelers=travelers,
            nights=nights,
            room_suggestions=rooms,
            hotels=hotels,
            disclaimer=self._disclaimer(hotels),
            provider=self._search_providers[0].name if self._search_providers else "none",
        )

    async def recommend_tiers(
        self,
        city: str,
        *,
        travelers: int,
        check_in: date,
        check_out: date,
        near: GeoPoint | None = None,
        per_tier: int = 1,
    ) -> TieredHotelRecommendation:
        """按「性价比 / 轻奢 / 高奢」三档各给出最合适的候选。

        与 :meth:`recommend` 的差异是**决策导向**：用户要的是「帮我挑一家」，
        所以按档位分组而非平铺；每档默认只给最优的 ``per_tier`` 家。

        某档位在目的地可能确实没有（如小城无五星），此时该档 ``hotels``
        为空——如实留空，不用低档酒店冒充高档。
        """
        nights = _validate(check_in, check_out, travelers)
        rooms = suggest_rooms(travelers)
        want = min(max(1, per_tier), _MAX_PER_TIER)

        # 三档并发检索：彼此独立，且各自内部已按距离排序
        results = await asyncio.gather(
            *[self._tier_cached(city, near, tier, want) for tier in _TIER_ORDER],
            return_exceptions=True,
        )

        tiers: list[TierOption] = []
        all_hotels: list[Hotel] = []
        for tier, result in zip(_TIER_ORDER, results, strict=True):
            hotels: list[Hotel] = [] if isinstance(result, BaseException) else list(result)
            if isinstance(result, BaseException):
                log.warning("hotel_tier_failed", tier=tier, error=f"{type(result).__name__}")
            if hotels and self.price_available:
                hotels = await self._attach_prices(
                    hotels, city, check_in, check_out, travelers, nights
                )
            all_hotels.extend(hotels)
            label, description = HOTEL_TIER_META[tier]
            tiers.append(
                TierOption(
                    tier=tier,
                    label=label,
                    description=description,
                    hotels=hotels,
                    basis=f"按高德住宿星级类目筛选（{label}）",
                )
            )

        log.info(
            "hotel_tiers_done",
            city=city,
            travelers=travelers,
            nights=nights,
            found={t.tier: len(t.hotels) for t in tiers},
            room_plan=describe_suggestions(rooms),
        )
        return TieredHotelRecommendation(
            city=city,
            check_in=check_in,
            check_out=check_out,
            travelers=travelers,
            nights=nights,
            room_suggestions=rooms,
            tiers=tiers,
            disclaimer=self._disclaimer(all_hotels),
            provider=self._search_providers[0].name if self._search_providers else "none",
        )

    async def _tier_cached(
        self,
        city: str,
        near: GeoPoint | None,
        tier: HotelTier,
        limit: int,
    ) -> list[Hotel]:
        """按档次检索并缓存。

        只走链上第一个可用 Provider 的 ``search_by_tier``；若其未实现该方法，
        抛 ``AttributeError`` 由调用方按空档处理（宁可空着也不混档）。
        """

        async def call() -> list[Hotel]:
            return await run_chain(
                "hotel_tier_search",
                self._search_providers,
                lambda p: _tier_search(p, city, near, tier, limit),
                self._settings,
            )

        # 缓存键必须含实际 Provider 名：分档检索走降级链，不同 Provider
        # 返回不同数据。若用固定字符串，切换 Provider（如测试替身 vs amap）
        # 会命中彼此的缓存，出现「A 的结果出现在 B 上」。
        chain_names = "|".join(p.name for p in self._search_providers)
        key = stable_key(
            "hotel_tier",
            provider=chain_names,
            city=city,
            near=near.osm_coord() if near else "",
            tier=tier,
            n=limit,
        )
        value, _hit = await self._cache.get_or_set(key, self._settings.cache_ttl_poi, call)
        raw: object = value
        return list(raw) if isinstance(raw, list) else []

    async def _search_cached(
        self,
        provider: HotelSearchProvider,
        city: str,
        near: GeoPoint | None,
        keyword: str,
        limit: int,
    ) -> list[Hotel]:
        key = stable_key(
            "hotel_search",
            provider=provider.name,
            city=city,
            near=near.amap_coord() if near else "",
            kw=keyword,
            n=limit,
        )

        async def call() -> list[Hotel]:
            # 必须传 async 函数而非「返回 coroutine 的 lambda」：
            # tenacity.AsyncRetrying 不会 await operation() 的返回值，
            # 那样会把未 await 的 coroutine 当作结果传出去。
            async def fetch() -> list[Hotel]:
                return await provider.search_hotels(city, near=near, keyword=keyword, limit=limit)

            return await with_retry(
                fetch,
                attempts=self._settings.external_retry_attempts,
                base_delay=self._settings.external_retry_base_delay,
            )

        value, _hit = await self._cache.get_or_set(key, self._settings.cache_ttl_poi, call)
        # 缓存层异常降级时可能返回非预期类型；宁可直连也不返回脏数据。
        # 用 object 中转做运行时校验，避免 mypy 因泛型已收窄而判为不可达。
        raw: object = value
        if not isinstance(raw, list):
            log.warning("hotel_search_cache_bad_value", provider=provider.name, value=type(raw))
            return await call()
        return raw

    async def _attach_prices(
        self,
        hotels: list[Hotel],
        city: str,
        check_in: date,
        check_out: date,
        travelers: int,
        nights: int,
    ) -> list[Hotel]:
        """为每家酒店查询房价。

        按价格 Provider 降级链逐个尝试：任一 Provider 给出报价即采用，
        全部失败或返回空则该酒店保持原有价格状态（参考价/无价）。
        """
        merged = list(hotels)
        for provider in self._price_providers:
            if not provider.configured:
                continue
            pending = [h for h in merged if h.price_confidence != "realtime"]
            if not pending:
                break
            results = await _gather_bounded(
                [
                    self._quote_one(provider, h, city, check_in, check_out, travelers)
                    for h in pending
                ]
            )
            quoted_map = {
                hotel.hotel_id: quoted
                for hotel, quoted in zip(pending, results, strict=True)
                if quoted is not None
            }
            if not quoted_map:
                continue
            merged = [
                h
                if h.hotel_id not in quoted_map
                else _merge_price(h, quoted_map[h.hotel_id], nights)
                for h in merged
            ]
            break
        return merged

    async def _quote_one(
        self,
        provider: HotelPriceProvider,
        hotel: Hotel,
        city: str,
        check_in: date,
        check_out: date,
        travelers: int,
    ) -> list[tuple[RoomType, float, str]] | None:
        if not provider.configured:
            return None
        key = stable_key(
            "hotel_price",
            provider=provider.name,
            name=hotel.name,
            city=city,
            ci=check_in.isoformat(),
            co=check_out.isoformat(),
            n=travelers,
        )

        async def call() -> list[tuple[RoomType, float, str]]:
            # 同 _search_cached：必须传 async 函数，避免 tenacity 返回未 await 的 coroutine
            async def fetch() -> list[tuple[RoomType, float, str]]:
                return await provider.quote(hotel.name, city, check_in, check_out, travelers)

            return await with_retry(
                fetch,
                attempts=self._settings.external_retry_attempts,
                base_delay=self._settings.external_retry_base_delay,
            )

        try:
            value, _hit = await self._cache.get_or_set(key, _PRICE_TTL_FALLBACK, call)
        except Exception as exc:  # 价格失败绝不影响酒店列表
            log.warning(
                "hotel_price_failed",
                provider=provider.name,
                hotel=hotel.name,
                error=f"{type(exc).__name__}: {exc}",
            )
            return None
        quoted_raw: object = value
        if not isinstance(quoted_raw, list):
            return None
        return list(quoted_raw) or None

    def _disclaimer(self, hotels: list[Hotel]) -> str:
        """生成数据边界说明。"""
        if not hotels:
            return "未找到住宿推荐，请更换城市或调整关键词后重试。"
        if not self.price_available:
            return _NO_REALTIME_DISCLAIMER
        if any(h.price_confidence == "realtime" for h in hotels):
            return "价格为查询时点的当日报价，可能随时变动，下单前请以平台实际价格为准。"
        if any(h.prices for h in hotels):
            return _NO_REALTIME_DISCLAIMER
        return _NO_PRICE_DISCLAIMER


async def _tier_search(
    provider: HotelSearchProvider,
    city: str,
    near: GeoPoint | None,
    tier: HotelTier,
    limit: int,
) -> list[Hotel]:
    """调用 Provider 的分档检索；未实现时退化为平铺检索后按档次过滤。

    退化路径用「过滤」而非「不分类」：把平铺结果里 ``tier`` 匹配的挑出来，
    效果与分档检索一致，只是召回面小些。实在匹配不到就返回空列表——
    绝不用其他档位的酒店充数。
    """
    search_by_tier = getattr(provider, "search_by_tier", None)
    if callable(search_by_tier):
        result: list[Hotel] = await search_by_tier(city, near=near, tier=tier, limit=limit)
        return result[:limit]
    hotels = await provider.search_hotels(city, near=near, keyword="", limit=25)
    return [h for h in hotels if h.tier == tier][:limit]


def _merge_price(
    hotel: Hotel,
    quoted: list[tuple[RoomType, float, str]],
    nights: int,
) -> Hotel:
    """把实时报价叠加到酒店上，保留原有参考价条目。"""
    prices: list[HotelPrice] = [
        HotelPrice(
            room_type=room_type,
            nightly_cny=nightly,
            nights=nights,
            total_cny=round(nightly * nights, 2),
            price_note=note,
        )
        for room_type, nightly, note in quoted
        if nightly > 0
    ]
    if not prices:
        return hotel
    return hotel.model_copy(
        update={
            "prices": prices,
            "price_confidence": "realtime",
            "price_source": "当日报价",
        }
    )


async def _gather_bounded[T](coros: list[Awaitable[T]]) -> list[T | None]:
    """并发执行并保持顺序；单项异常置 None（价格失败不影响酒店列表）。"""
    results = await asyncio.gather(*coros, return_exceptions=True)
    return [None if isinstance(item, BaseException) else item for item in results]


def _validate(check_in: date, check_out: date, travelers: int) -> int:
    if not 1 <= travelers <= 50:
        raise ValueError("出行人数必须在 1..50 之间")
    if check_out <= check_in:
        raise ValueError("离店日期必须晚于入住日期")
    nights = (check_out - check_in).days
    if nights > 365:
        raise ValueError("单次查询最长支持 365 晚")
    return nights


def _default_search_providers(settings: Settings) -> list[HotelSearchProvider]:
    return [AMapHotelProvider(settings)]


def _default_price_providers(settings: Settings) -> list[HotelPriceProvider]:
    """价格 Provider 列表。

    当前未内置 OTA 实时报价 Provider——直连携程/美团需要签名与反爬对抗，
    既不稳定也可能违反其服务条款。接口已就位，接入官方或合规渠道只需在此
    追加一个实现（满足 :class:`HotelPriceProvider` 即可），服务层无需改动。
    """
    return []
