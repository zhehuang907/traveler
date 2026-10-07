"""高德酒店 Provider：真实酒店 + 参考均价。

数据性质（重要）
----------------
高德 POI 接口返回的 ``biz_ext.lowest_price`` / ``biz_ext.cost`` 是**参考均价**，
不是「指定入住日期的实时房价」。因此本 Provider 一律把价格标为
``reference``（参考价），并在 ``price_source`` 注明来源，
**绝不冒充实时报价**。

高德按住宿类目细分（``types`` 参数），可优先搜高星与经济型两类，
让用户在有限条数里同时看到两端选择。
"""

from __future__ import annotations

import math
from typing import Final, cast

from pydantic import HttpUrl

from travel_agent.config import Settings
from travel_agent.domain.models import (
    GeoPoint,
    Hotel,
    HotelPrice,
    HotelTier,
    RoomType,
)
from travel_agent.logging_conf import get_logger
from travel_agent.services.errors import ProviderError
from travel_agent.services.http_client import build_client

log = get_logger(component="hotel-amap")

_BASE_URL = "https://restapi.amap.com/v3"
#: 住宿服务大类。高德编码中 050000=住宿服务、060000=购物服务（勿混淆）。
_LODGING_TYPES: Final = "050000"
#: 判定「确实是酒店」的类目前缀。实测高德 types 检索会混入餐饮/商场
#: （如 types=050000 会返回「银龙坞山庄」餐饮、「络腮胡掏羊锅」火锅），
#: 因此必须按类目字符串强校验，不能只信接口返回。
_LODGING_TYPE_PREFIX: Final = "住宿服务"
#: 地球半径（米），Haversine 用
_EARTH_RADIUS_M: Final = 6_371_000.0
#: 检索关键词候选。按顺序尝试，取第一个有结果的。
#: 高德 keywords 与 types 同时给出会互斥返回 0 条，故 keywords 单独使用。
_SEARCH_KEYWORDS: Final = ("住宿", "宾馆酒店", "酒店")

#: 档次 -> 召回关键词。关键词只负责「把该档位的酒店召回」，
#: 实际归档仍以返回项的末级类目为准（见 ``_tier_of``），二者分工明确。
_TIER_KEYWORDS: Final[dict[HotelTier, tuple[str, ...]]] = {
    "luxury": ("五星级酒店", "五星级宾馆", "豪华酒店"),
    "comfort": ("四星级酒店", "四星级宾馆", "高档酒店"),
    "value": ("经济型酒店", "快捷酒店", "经济型连锁酒店", "民宿"),
}

#: 高德末级类目 -> 档次。实测高德 type 形如
#: 「住宿服务;宾馆酒店;五星级宾馆」，末级类目才是真正的档位依据。
_TIER_BY_CATEGORY: Final[tuple[tuple[str, HotelTier], ...]] = (
    ("五星", "luxury"),
    ("豪华", "luxury"),
    ("奢华", "luxury"),
    ("四星", "comfort"),
    ("高档", "comfort"),
    ("三星", "comfort"),
    ("经济型", "value"),
    ("连锁", "value"),
    ("快捷", "value"),
    ("旅馆", "value"),
    ("招待所", "value"),
    ("青年旅舍", "value"),
    ("民宿", "value"),
)

#: 关键词 -> 展示房型。高德不给房型，此映射把「搜什么」与「推荐什么」对齐：
#: 例如搜大床房时，返回的酒店更可能提供大床房。
_KEYWORD_ROOM_HINT: Final[dict[str, RoomType]] = {
    "大床": "double",
    "双床": "twin",
    "家庭": "family",
    "亲子": "family",
    "三人": "triple",
    "套房": "suite",
    "单人": "single",
}


class AMapHotelProvider:
    """高德酒店检索。key 为空时由降级链跳过。"""

    name = "amap"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.amap_configured

    async def search_hotels(
        self,
        city: str,
        *,
        near: GeoPoint | None = None,
        keyword: str = "",
        limit: int = 10,
    ) -> list[Hotel]:
        """检索住宿类 POI。

        高德 ``/place/text`` 的两个实测陷阱（已踩，勿改回）：

        1. ``keywords`` 与 ``types`` **同时**给出时两条件互斥，返回 0 条
           （``keywords=酒店&types=050000`` count=0；仅 ``types=050000`` count=6）。
           因此这里只传 ``keywords``。
        2. ``types`` 检索会混入非住宿 POI（餐饮、商场），必须按 ``type``
           字符串以「住宿服务」开头做强校验。
        """
        wanted = max(1, min(limit, 25))
        hotels: list[Hotel] = []
        seen: set[str] = set()
        # 多关键词合并去重后再统一排序/截断。
        # 不逐词凑够就 break——那会让后续关键词的远端酒店挤掉前一轮的近端酒店。
        for kw in self._keywords(keyword):
            body = await self._get(
                "/place/text",
                {
                    "keywords": kw,
                    "city": city,
                    "citylimit": "true",
                    "extensions": "all",
                    "offset": str(min(25, max(wanted, 10))),
                    "page": "1",
                },
            )
            raw_pois = body.get("pois")
            if not isinstance(raw_pois, list):
                continue
            for raw in raw_pois:
                hotel = _parse_hotel(raw)
                if hotel is None or hotel.hotel_id in seen:
                    continue
                seen.add(hotel.hotel_id)
                hotels.append(hotel)
            if len(hotels) >= wanted * 2:
                # 已有足够冗余量，再取也不会更优
                break

        if near is not None:
            hotels = _sort_by_distance(hotels, near)
        return hotels[:wanted]

    def _keywords(self, keyword: str) -> tuple[str, ...]:
        """自定义关键词优先，其次回落到候选序列。"""
        if keyword and keyword not in _SEARCH_KEYWORDS:
            return (keyword, *_SEARCH_KEYWORDS)
        return _SEARCH_KEYWORDS

    async def search_by_tier(
        self,
        city: str,
        *,
        near: GeoPoint | None = None,
        tier: HotelTier,
        limit: int = 3,
    ) -> list[Hotel]:
        """按档次检索酒店。

        先用该档位的关键词召回，再**按返回项末级类目二次过滤**——关键词
        命中不等于档次正确（实测「精品酒店」会召回五星级、「经济型酒店」
        会召回旅馆招待所）。过滤后若结果不足 ``limit``，用通用关键词补齐，
        宁可给出档次偏低的候选，也不让该档位空着。
        """
        wanted = max(1, min(limit, 10))
        matched: list[Hotel] = []
        seen: set[str] = set()

        for kw in _TIER_KEYWORDS[tier]:
            raw_pois = await self._search_raw(city, kw)
            for raw in raw_pois:
                hotel = _parse_hotel(raw)
                if hotel is None or hotel.hotel_id in seen:
                    continue
                seen.add(hotel.hotel_id)
                # 必须既命中档次，又带明确住宿星级类目。
                # 只看档次会误收：实测「四星级酒店」召回的某饭店末级类目是
                # 「餐饮相关」——那是酒店附设餐厅，不是酒店本身。
                if hotel.tier == tier and _has_lodging_grade(hotel.categories):
                    matched.append(hotel)
            if len(matched) >= wanted:
                break

        if len(matched) < wanted:
            for kw in _SEARCH_KEYWORDS:
                for raw in await self._search_raw(city, kw):
                    hotel = _parse_hotel(raw)
                    if hotel is None or hotel.hotel_id in seen:
                        continue
                    seen.add(hotel.hotel_id)
                    if _has_lodging_grade(hotel.categories):
                        matched.append(hotel)
                    if len(matched) >= wanted:
                        break
                if len(matched) >= wanted:
                    break

        if near is not None:
            matched = _sort_by_distance(matched, near)
        return matched[:wanted]

    async def _search_raw(self, city: str, keyword: str) -> list[object]:
        """执行一次 POI 文本检索，返回原始条目。"""
        body = await self._get(
            "/place/text",
            {
                "keywords": keyword,
                "city": city,
                "citylimit": "true",
                "extensions": "all",
                "offset": "20",
                "page": "1",
            },
        )
        raw_pois = body.get("pois")
        return raw_pois if isinstance(raw_pois, list) else []

    async def _get(self, path: str, extra: dict[str, str]) -> dict[str, object]:
        params = {"key": self._settings.amap_api_key.get_secret_value(), **extra}
        async with build_client(self._settings) as client:
            response = await client.get(f"{_BASE_URL}{path}", params=params)
            response.raise_for_status()
            body = cast(dict[str, object], response.json())
        if str(body.get("status")) != "1":
            raise ProviderError(f"高德接口 {path} 返回 status={body.get('status')}")
        return body


def _parse_hotel(raw: object) -> Hotel | None:
    if not isinstance(raw, dict) or not raw.get("id") or not raw.get("name"):
        return None
    type_text = str(raw.get("type", ""))
    # 强校验：非「住宿服务」类目一律丢弃（实测会混入餐饮/商场）
    if not type_text.startswith(_LODGING_TYPE_PREFIX):
        return None
    location = raw.get("location")
    if not isinstance(location, str) or "[" in location:
        return None
    try:
        point = _parse_coord(location)
    except ProviderError:
        return None

    raw_biz = raw.get("biz_ext")
    biz: dict[str, object] = raw_biz if isinstance(raw_biz, dict) else {}
    name = str(raw["name"])
    hotel_id = str(raw["id"])

    categories = [c for c in str(raw.get("type", "")).split(";") if c]
    ref_price = _to_price(biz.get("lowest_price")) or _to_price(biz.get("cost"))
    star = _to_str(biz.get("star"))
    tel = _to_str(raw.get("tel"))

    # 按酒店名推断适配房型（高德不提供房型）
    room_type = _guess_room_type(name)
    prices: list[HotelPrice] = []
    if ref_price is not None:
        prices.append(
            HotelPrice(
                room_type=room_type,
                nightly_cny=ref_price,
                nights=1,
                price_note="高德参考均价，非指定日期实时房价",
            )
        )

    return Hotel(
        hotel_id=hotel_id,
        name=name,
        address=_to_str(raw.get("address")),
        location=point,
        categories=categories,
        rating=_to_rating(biz.get("rating")),
        star=star,
        tier=_tier_of(categories),
        tel=tel,
        prices=prices,
        price_confidence="reference" if ref_price is not None else "unavailable",
        price_source="高德参考均价",
        sources=[HttpUrl(f"https://www.amap.com/place/{hotel_id}")],
        provider="amap",
    )


def _parse_coord(value: str) -> GeoPoint:
    try:
        lng_text, lat_text = value.split(",", 1)
        return GeoPoint(lat=float(lat_text), lng=float(lng_text))
    except (ValueError, AttributeError) as exc:
        raise ProviderError(f"高德坐标格式非法: {value!r}") from exc


#: 判定「末级类目确实是住宿」而非附属设施。高德 type 常形如
#: 「住宿服务;宾馆酒店;四星级宾馆|餐饮服务;餐饮相关场所;中餐厅」——
#: 末级落到「中餐厅」说明这是附设餐饮，不应作为酒店推荐。
_LODGING_LEAF_MARKERS: Final = ("宾馆", "酒店", "旅馆", "旅舍", "民宿", "招待所", "山庄")


def _has_lodging_grade(categories: list[str]) -> bool:
    """末级类目是否为真正的住宿类目。"""
    if not categories:
        return False
    leaf = categories[-1]
    return any(marker in leaf for marker in _LODGING_LEAF_MARKERS)


def _tier_of(categories: list[str]) -> HotelTier | None:
    """按高德类目判定住宿档次。

    从**末级类目**（``categories[-1]``，如「五星级宾馆」「经济型连锁酒店」）
    向前匹配。末级类目最精确；若末级无法判定（如「餐饮相关」混入项），
    再回退到整条 type 链，避免漏判。
    """
    if not categories:
        return None
    for category in (categories[-1], *categories):
        for keyword, tier in _TIER_BY_CATEGORY:
            if keyword in category:
                return tier
    return None


def _guess_room_type(name: str) -> RoomType:
    """按酒店名关键词推断主推房型。"""
    for keyword, room_type in _KEYWORD_ROOM_HINT.items():
        if keyword in name:
            return room_type
    return "twin"


def _sort_by_distance(hotels: list[Hotel], origin: GeoPoint) -> list[Hotel]:
    """按直线距离升序并回填 ``distance_m``。

    先算距离再排序：先排序会漏掉未标记的项。
    """
    with_distance: list[Hotel] = []
    for hotel in hotels:
        dist = _haversine_m(origin, hotel.location)
        with_distance.append(
            hotel.model_copy(update={"distance_m": dist}) if dist is not None else hotel
        )
    return sorted(with_distance, key=lambda h: (h.distance_m is None, h.distance_m or 0))


def _haversine_m(a: GeoPoint, b: GeoPoint) -> int | None:
    """Haversine 直线距离（米）。

    用平面近似足够：酒店筛选场景下直线距离比步行距离更直观，
    且避免为每家酒店额外发起路径规划请求（配额与延迟双重考虑）。
    """
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = math.radians(b.lat - a.lat)
    dl = math.radians(b.lng - a.lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h)))


def _to_price(value: object) -> float | None:
    """高德价格字段可能是数值，也可能是列表（如 ``[100]``）或空 ``[]``。"""
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None or isinstance(value, (dict, list)):
        return None
    try:
        price = float(str(value).replace("¥", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    # 高德对无价格的商家返回 0 / [] / "0"
    return price if price > 0.0 else None


def _to_rating(value: object) -> float | None:
    if isinstance(value, list):
        value = value[0] if value else None
    try:
        rating = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return rating if 0.0 < rating <= 5.0 else None


def _to_str(value: object) -> str:
    if isinstance(value, list):
        value = " / ".join(str(v) for v in value if v) if value else None
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"[]", "{}", "0"} else text
