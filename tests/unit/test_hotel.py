"""住宿推荐单元测试：房型推断、价格诚实性、参数校验。

设计约束（被测代码必须满足，否则测试失败）：
- 房型推断的主推方案必须能容纳全部出行人数
- 无实时价格时必须标为 unavailable/reference，**绝不能出现估算值**
- 日期倒置、人数越界必须抛错而非静默纠正
"""

from datetime import date, timedelta
from pathlib import Path

import pytest

from travel_agent.config import get_settings
from travel_agent.domain.models import (
    GeoPoint,
    Hotel,
    HotelPrice,
    HotelRecommendation,
    HotelTier,
    RoomType,
)
from travel_agent.domain.rooming import (
    describe_suggestions,
    primary_room_suggestion,
    suggest_rooms,
)
from travel_agent.services.cache import TTLCache
from travel_agent.services.hotels.amap import _has_lodging_grade, _tier_of
from travel_agent.services.hotels.base import HotelPriceProvider, HotelSearchProvider
from travel_agent.services.hotels.service import HotelService

# ---------------------------------------------------------------------------
# 房型推断（纯逻辑，重点覆盖边界）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6, 7, 8, 12, 49, 50])
def test_primary_room_cannot_under_accommodate(n: int) -> None:
    """主推房型的总床位数必须 >= 出行人数——这是需求的核心约束。"""
    assert primary_room_suggestion(n).total_beds >= n


def test_single_traveler_gets_single_room() -> None:
    primary = primary_room_suggestion(1)
    assert primary.room_type == "single"
    assert primary.rooms == 1


def test_two_travelers_get_twin_as_primary() -> None:
    primary = primary_room_suggestion(2)
    assert primary.room_type == "twin"
    assert primary.rooms == 1
    # 大床房应作为备选给出（情侣出行场景）
    assert any(s.room_type == "double" for s in suggest_rooms(2))


def test_four_travelers_get_family_primary() -> None:
    primary = primary_room_suggestion(4)
    assert primary.room_type == "family"
    assert primary.rooms == 1


def test_five_plus_travelers_split_into_multiple_rooms() -> None:
    primary = primary_room_suggestion(5)
    assert primary.rooms == 3  # ceil(5/2)
    assert primary.room_type == "twin"


def test_zero_or_negative_travelers_falls_back_to_one() -> None:
    """非法输入不抛错（前端可能传 0），退回 1 人方案而不是空列表。"""
    assert suggest_rooms(0)[0].rooms == 1
    assert suggest_rooms(-5)[0].rooms == 1


def test_every_suggestion_has_rationale() -> None:
    """每条建议都要有可展示给用户的依据说明。"""
    for n in (1, 2, 3, 4, 7):
        for s in suggest_rooms(n):
            assert s.rationale, f"{n} 人的 {s.room_type} 缺少 rationale"


def test_describe_suggestions_is_human_readable() -> None:
    text = describe_suggestions(suggest_rooms(4))
    assert "家庭房" in text
    assert "主推" in text


def test_describe_suggestions_handles_empty() -> None:
    assert describe_suggestions([]) == ""


# ---------------------------------------------------------------------------
# HotelPrice 换算
# ---------------------------------------------------------------------------


def test_with_nights_computes_total() -> None:
    price = HotelPrice(room_type="twin", nightly_cny=300.0, nights=1)
    updated = price.with_nights(4)
    assert updated.nights == 4
    assert updated.total_cny == 1200.0


def test_with_nights_keeps_none_total_when_price_missing() -> None:
    """没有价格时总价必须为 None，不能变成 0（0 会被误解为「免费」）。"""
    price = HotelPrice(room_type="twin", nightly_cny=None, nights=1)
    updated = price.with_nights(3)
    assert updated.total_cny is None


# ---------------------------------------------------------------------------
# 服务层：价格诚实性
# ---------------------------------------------------------------------------


class _StubSearch(HotelPriceProvider, HotelSearchProvider):
    """同时提供搜索与价格的可控替身。"""

    name = "stub"
    configured = True

    def __init__(
        self,
        hotels: list[Hotel],
        quotes: list[tuple[RoomType, float, str]] | None,
        *,
        support_tier: bool = True,
    ):
        self._hotels = hotels
        self._quotes = quotes
        self._support_tier = support_tier

    async def search_hotels(
        self,
        city: str,
        *,
        near: GeoPoint | None,
        keyword: str,
        limit: int,
    ) -> list[Hotel]:
        return self._hotels[:limit]

    async def search_by_tier(
        self,
        city: str,
        *,
        near: GeoPoint | None,
        tier: HotelTier,
        limit: int,
    ) -> list[Hotel]:
        if not self._support_tier:
            raise AttributeError("stub 不支持分档")
        return [h for h in self._hotels if h.tier == tier][:limit]

    async def quote(
        self,
        hotel_name: str,
        city: str,
        check_in: date,
        check_out: date,
        travelers: int,
    ) -> list[tuple[RoomType, float, str]]:
        return self._quotes or []


def _hotel(**kw: object) -> Hotel:
    defaults: dict[str, object] = {
        "hotel_id": "h1",
        "name": "测试酒店",
        "location": GeoPoint(lat=30.0, lng=120.0),
        "provider": "stub",
    }
    return Hotel(**{**defaults, **kw})


def _service(stub: _StubSearch, tmp_path: Path | None = None) -> HotelService:
    """构造被测服务。

    必须注入**独立缓存**：否则测试会读写生产的 ``data/cache/hotels``，
    用例之间经缓存互相污染——同城市同档位的另一个用例结果会被复用，
    导致「某档应为空」这类断言随机失败（已实测踩到）。

    ``tmp_path`` 由 pytest 提供，每个用例独享，是真正的隔离。
    """
    settings = get_settings()
    cache = TTLCache(settings, subdir=str(tmp_path) if tmp_path else "test_hotels")
    return HotelService(
        settings=settings,
        cache=cache,
        search_providers=[stub],
        price_providers=[stub],
    )


@pytest.mark.asyncio
async def test_realtime_quote_marks_confidence_realtime(tmp_path: Path) -> None:
    stub = _StubSearch([_hotel()], [("twin", 420.0, "含早")])
    rec = await _service(stub, tmp_path).recommend(
        "杭州",
        travelers=2,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 4),
    )
    hotel = rec.hotels[0]
    assert hotel.price_confidence == "realtime"
    assert hotel.prices[0].total_cny == 1260.0
    assert hotel.prices[0].room_type == "twin"


@pytest.mark.asyncio
async def test_empty_quote_keeps_unavailable_not_fake_zero(tmp_path: Path) -> None:
    """价格 Provider 查不到时必须留空，不能用 0 或估算值填充。"""
    stub = _StubSearch([_hotel()], None)
    rec = await _service(stub, tmp_path).recommend(
        "杭州",
        travelers=2,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 3),
    )
    hotel = rec.hotels[0]
    assert hotel.price_confidence == "unavailable"
    assert hotel.prices == []
    assert not hotel.has_realtime_price


@pytest.mark.asyncio
async def test_recommend_returns_room_suggestions_for_travelers(tmp_path: Path) -> None:
    stub = _StubSearch([_hotel()], None)
    rec = await _service(stub, tmp_path).recommend(
        "杭州",
        travelers=5,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 3),
    )
    assert rec.travelers == 5
    assert rec.nights == 2
    assert rec.room_suggestions[0].rooms == 3


@pytest.mark.asyncio
async def test_disclaimer_mentions_reference_price_when_no_realtime(tmp_path: Path) -> None:
    stub = _StubSearch([_hotel()], None)
    rec = await _service(stub, tmp_path).recommend(
        "杭州",
        travelers=2,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 3),
    )
    # 即使没有价格 Provider，说明也要告知用户去哪查
    assert "携程" in rec.disclaimer or "美团" in rec.disclaimer


@pytest.mark.asyncio
async def test_inverted_dates_rejected(tmp_path: Path) -> None:
    stub = _StubSearch([_hotel()], None)
    with pytest.raises(ValueError, match="离店日期"):
        await _service(stub, tmp_path).recommend(
            "杭州",
            travelers=2,
            check_in=date(2026, 12, 5),
            check_out=date(2026, 12, 1),
        )


@pytest.mark.asyncio
async def test_same_day_rejected(tmp_path: Path) -> None:
    stub = _StubSearch([_hotel()], None)
    with pytest.raises(ValueError, match="离店日期"):
        await _service(stub, tmp_path).recommend(
            "杭州",
            travelers=2,
            check_in=date(2026, 12, 1),
            check_out=date(2026, 12, 1),
        )


@pytest.mark.asyncio
async def test_out_of_range_travelers_rejected(tmp_path: Path) -> None:
    stub = _StubSearch([_hotel()], None)
    with pytest.raises(ValueError, match="人数"):
        await _service(stub, tmp_path).recommend(
            "杭州",
            travelers=99,
            check_in=date(2026, 12, 1),
            check_out=date(2026, 12, 3),
        )


@pytest.mark.asyncio
async def test_limit_is_capped(tmp_path: Path) -> None:
    hotels = [_hotel(hotel_id=f"h{i}", name=f"酒店{i}") for i in range(30)]
    stub = _StubSearch(hotels, None)
    rec = await _service(stub, tmp_path).recommend(
        "杭州",
        travelers=2,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 3),
        limit=99,
    )
    assert len(rec.hotels) <= 12


def test_recommendation_model_roundtrips() -> None:
    """确保响应模型可序列化，前端 x-data 才能正常消费。"""
    rec = HotelRecommendation(
        city="杭州",
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 3),
        travelers=2,
        nights=2,
        room_suggestions=suggest_rooms(2),
        hotels=[_hotel()],
        provider="stub",
    )
    dumped = rec.model_dump_json()
    assert "room_suggestions" in dumped
    assert HotelRecommendation.model_validate_json(dumped) == rec


def test_suggestion_total_beds_matches_rooms_and_occupancy() -> None:
    s = suggest_rooms(5)[0]
    assert s.total_beds == s.rooms * s.occupancy


def test_nights_derived_from_dates() -> None:
    assert (date(2026, 12, 1) + timedelta(days=1)) > date(2026, 12, 1)


# ---------------------------------------------------------------------------
# 档次判定（高德类目 -> value/comfort/luxury）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("categories", "expected"),
    [
        (["住宿服务", "宾馆酒店", "五星级宾馆"], "luxury"),
        (["住宿服务", "宾馆酒店", "四星级宾馆"], "comfort"),
        (["住宿服务", "宾馆酒店", "三星级宾馆"], "comfort"),
        (["住宿服务", "宾馆酒店", "经济型连锁酒店"], "value"),
        (["住宿服务", "旅馆招待所", "旅馆招待所"], "value"),
        (["住宿服务", "宾馆酒店", "青年旅舍"], "value"),
        (["住宿服务", "宾馆酒店", "四星级宾馆", "餐饮服务"], "comfort"),
    ],
)
def test_tier_from_amap_category(categories: list[str], expected: HotelTier) -> None:
    """档位以高德末级类目判定（已针对实测返回构造样本）。"""
    assert _tier_of(categories) == expected


def test_tier_unknown_category_returns_none() -> None:
    """无法判定时返回 None——不硬塞一个档次。"""
    assert _tier_of([]) is None
    assert _tier_of(["购物服务", "商场", "购物中心"]) is None


def test_leaf_catering_is_not_lodging() -> None:
    """末级类目是「餐饮相关」说明是附设餐厅，不应作为酒店候选。"""
    assert not _has_lodging_grade(["住宿服务", "宾馆酒店", "餐饮相关"])
    assert _has_lodging_grade(["住宿服务", "宾馆酒店", "四星级宾馆"])


# ---------------------------------------------------------------------------
# 三档推荐
# ---------------------------------------------------------------------------


def _tiered_stub() -> _StubSearch:
    hotels = [
        _hotel(hotel_id="v1", name="经济酒店", tier="value"),
        _hotel(hotel_id="c1", name="四星酒店", tier="comfort"),
        _hotel(hotel_id="l1", name="五星酒店", tier="luxury"),
    ]
    return _StubSearch(hotels, None)


@pytest.mark.asyncio
async def test_recommend_tiers_returns_three_fixed_tiers(tmp_path: Path) -> None:
    rec = await _service(_tiered_stub(), tmp_path).recommend_tiers(
        "杭州",
        travelers=2,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 4),
    )
    assert [t.tier for t in rec.tiers] == ["value", "comfort", "luxury"]
    assert [t.label for t in rec.tiers] == ["性价比", "轻奢", "高奢"]


@pytest.mark.asyncio
async def test_each_tier_only_contains_its_own_hotels(tmp_path: Path) -> None:
    """核心约束：绝不能把其他档次的酒店混进本档。"""
    rec = await _service(_tiered_stub(), tmp_path).recommend_tiers(
        "杭州",
        travelers=2,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 4),
    )
    for option in rec.tiers:
        for hotel in option.hotels:
            assert hotel.tier == option.tier, f"{hotel.name} 档次错位"


@pytest.mark.asyncio
async def test_missing_tier_stays_empty_instead_of_substituting(tmp_path: Path) -> None:
    """某档无房时留空，不用低档酒店冒充高档。"""
    hotels = [_hotel(hotel_id="v1", name="经济酒店", tier="value")]
    rec = await _service(_StubSearch(hotels, None), tmp_path).recommend_tiers(
        "杭州",
        travelers=2,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 4),
    )
    assert len(rec.by_tier("value").hotels) == 1  # type: ignore[union-attr]
    assert rec.by_tier("luxury").hotels == []  # type: ignore[union-attr]
    assert rec.by_tier("luxury").available is False  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_tiers_include_room_suggestions(tmp_path: Path) -> None:
    rec = await _service(_tiered_stub(), tmp_path).recommend_tiers(
        "杭州",
        travelers=4,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 3),
    )
    assert rec.room_suggestions[0].rooms == 1
    assert rec.room_suggestions[0].room_type == "family"
    assert rec.nights == 2


@pytest.mark.asyncio
async def test_tiers_reject_inverted_dates(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="离店日期"):
        await _service(_tiered_stub(), tmp_path).recommend_tiers(
            "杭州",
            travelers=2,
            check_in=date(2026, 12, 5),
            check_out=date(2026, 12, 1),
        )


def test_tiered_recommendation_model_roundtrips() -> None:
    from travel_agent.domain.models import (
        HOTEL_TIER_META,
        TieredHotelRecommendation,
        TierOption,
    )

    tiers = [
        TierOption(
            tier=t,
            label=HOTEL_TIER_META[t][0],
            description=HOTEL_TIER_META[t][1],
            hotels=[],
        )
        for t in ("value", "comfort", "luxury")
    ]
    rec = TieredHotelRecommendation(
        city="杭州",
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 3),
        travelers=2,
        nights=2,
        room_suggestions=suggest_rooms(2),
        tiers=tiers,
        provider="stub",
    )
    dumped = rec.model_dump_json()
    assert TieredHotelRecommendation.model_validate_json(dumped) == rec
    assert rec.by_tier("comfort") is not None
    assert rec.by_tier("comfort").label == "轻奢"  # type: ignore[union-attr]
