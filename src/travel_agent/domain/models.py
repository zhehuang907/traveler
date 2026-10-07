"""领域模型：外部世界信息进入 Agent 前的统一形态。

本模块是纯内核：禁止 import services/agent，禁止任何 IO。
所有 Provider 返回的裸 dict 必须先映射成本处的 Pydantic 模型，
层间传递不允许出现未经校验的 dict。
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

# 出行方式：高德/OSM 路径规划的统一入参
TravelMode = Literal["walking", "transit", "driving"]


class DomainModel(BaseModel):
    """领域模型基类：frozen 防误改、额外属性报错防字段漂移。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


class GeoPoint(DomainModel):
    """WGS-84 经纬度（纬度在前）。各家坐标系差异在 Provider 内完成转换。"""

    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)

    def amap_coord(self) -> str:
        """高德参数顺序：经度,纬度（GCJ-02，由高德侧保证）。"""
        return f"{self.lng},{self.lat}"

    def osm_coord(self) -> str:
        """OSM/Overpass 参数顺序：纬度,经度。"""
        return f"{self.lat},{self.lng}"


class SearchResult(DomainModel):
    """通用网页搜索的一条结果，事实溯源的最小单元。"""

    title: str = Field(min_length=1)
    url: HttpUrl
    snippet: str = ""
    published_at: date | None = None
    provider: str = Field(min_length=1)


class Poi(DomainModel):
    """景点/餐厅/酒店等兴趣点。坐标与来源链接必备。"""

    poi_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    categories: list[str] = Field(default_factory=list)
    address: str = ""
    location: GeoPoint
    rating: float | None = Field(default=None, ge=0.0, le=10.0)
    business_hours: str | None = None
    cost_hint: str | None = None
    sources: list[HttpUrl] = Field(default_factory=list)
    provider: str = Field(min_length=1)


class DailyWeather(DomainModel):
    """逐日天气。缺失字段为 None（如和风 7 日接口不含降水概率/紫外线）。"""

    date: date
    temp_min: float
    temp_max: float
    condition: str
    precip_prob: int | None = Field(default=None, ge=0, le=100)
    wind_speed: float | None = Field(default=None, ge=0.0)
    uv_index: float | None = Field(default=None, ge=0.0)
    sunrise: time | None = None
    sunset: time | None = None
    # True 表示该值来自历史同期气候均值，不是实时预报（超预报窗口时）
    climate: bool = False

    @property
    def rainy(self) -> bool:
        """降水概率 > 60% 视为雨天（驱动室内备选编排）。"""
        return self.precip_prob is not None and self.precip_prob > 60


class WeatherForecast(DomainModel):
    """一次天气查询的完整结果。"""

    city: str = Field(min_length=1)
    location: GeoPoint | None = None
    days: list[DailyWeather] = Field(default_factory=list)
    provider: str = Field(min_length=1)
    is_climate_reference: bool = False


class RouteInfo(DomainModel):
    """两点间单方式路线耗时。"""

    origin: GeoPoint
    destination: GeoPoint
    mode: TravelMode
    distance_m: int = Field(ge=0)
    duration_s: int = Field(ge=0)
    provider: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# 酒店推荐
# ---------------------------------------------------------------------------

#: 房型类别。与「住几个人」解耦，供前端展示与后续按房型比价。
RoomType = Literal["single", "double", "twin", "triple", "family", "suite"]

#: 价格的可信等级。前端据此决定是否展示「参考价」字样，避免把非实时数据
#: 呈现为实时报价——这是本模块最重要的诚实性约束。
#:
#: - ``realtime`` : 来自按「酒店名+城市+入住日期」精确查询到的当日报价
#: - ``reference``: 来源方给出的参考均价（如高德 lowest_price），非当日实时
#: - ``unavailable``: 未取到价格，字段为 None，前端显示「暂无报价」
PriceConfidence = Literal["realtime", "reference", "unavailable"]

#: 住宿档次。顺序即推荐顺序（从省钱到享受）。
#:
#: 划分依据是**高德 POI 的末级类目**（实测可稳定取得），而非价格——
#: 高德的 ``lowest_price`` 覆盖率很低（实测 3 家高星酒店仅 1 家有价），
#: 用价格分档会把大量真实酒店误判为「无档位」。
HotelTier = Literal["value", "comfort", "luxury"]

#: 档次展示元数据（中文名与说明），供 API/前端统一取用避免各端各写一份
HOTEL_TIER_META: Final[dict[HotelTier, tuple[str, str]]] = {
    "value": ("性价比", "位置或设施略有取舍，价格友好"),
    "comfort": ("轻奢", "四星左右，位置与服务均衡"),
    "luxury": ("高奢", "五星及以上，设施与服务顶级"),
}


class RoomSuggestion(DomainModel):
    """按出行人数推断出的房型建议。

    只做「该开几间、每间什么房」，不承诺具体酒店的房型库存——
    真实可售房型以酒店实际为准。
    """

    #: 建议房型
    room_type: RoomType
    #: 该房型下每间入住人数
    occupancy: int = Field(ge=1, le=10)
    #: 需要几间
    rooms: int = Field(ge=1, le=25)
    #: 面向用户的说明，如「1 位成人，建议单人间」
    rationale: str = Field(default="", max_length=200)

    @property
    def total_beds(self) -> int:
        return self.occupancy * self.rooms


class HotelPrice(DomainModel):
    """单个房型在某入住区间的价格。"""

    room_type: RoomType
    #: 每晚价（元）。None 表示未取到
    nightly_cny: float | None = Field(default=None, ge=0.0)
    #: 入住晚数
    nights: int = Field(ge=1, le=365)
    #: 总价 = nightly_cny * nights；nightly_cny 为 None 时为 None
    total_cny: float | None = Field(default=None, ge=0.0)
    #: 价格是否含税/早餐等，来源无法确认时留空
    price_note: str = Field(default="", max_length=120)

    def with_nights(self, nights: int) -> HotelPrice:
        """换算为指定晚数后的价格（保持与原价的推导一致）。"""
        if self.nightly_cny is None:
            return self.model_copy(update={"nights": nights, "total_cny": None})
        return self.model_copy(
            update={"nights": nights, "total_cny": round(self.nightly_cny * nights, 2)}
        )


class Hotel(DomainModel):
    """酒店候选。高德等 POI 来源提供真实存在性，价格层为可选叠加。"""

    hotel_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(default="", max_length=200)
    location: GeoPoint
    #: 高德 type 原始分类，如「住宿服务;宾馆酒店;四星级宾馆」
    categories: list[str] = Field(default_factory=list)
    #: 评分（0-5），来源无评分时为 None
    rating: float | None = Field(default=None, ge=0.0, le=5.0)
    #: 星级（字符串形式，如「4」「经济」），来源可能给数字或空
    star: str | None = Field(default=None, max_length=20)
    #: 住宿档次，由高德末级类目判定；无法判定时留空而非硬塞
    tier: HotelTier | None = None
    tel: str = Field(default="", max_length=60)
    #: 到行程首个/核心点的直线距离（米），无位置时为 None
    distance_m: int | None = Field(default=None, ge=0)
    #: 价格列表（可能为空——拿不到价就明确为空，不编造）
    prices: list[HotelPrice] = Field(default_factory=list)
    #: 整体价格可信等级，取自 prices 中最可信的一条
    price_confidence: PriceConfidence = "unavailable"
    #: 价格取数时间，前端展示「更新于 …」
    price_checked_at: datetime | None = None
    #: 价格来源说明，如「高德参考均价」「携程当日报价」
    price_source: str = Field(default="", max_length=60)
    sources: list[HttpUrl] = Field(default_factory=list)
    provider: str = Field(min_length=1)

    @property
    def has_realtime_price(self) -> bool:
        return any(p.nightly_cny is not None for p in self.prices)


class HotelRecommendation(DomainModel):
    """一次酒店推荐的完整结果，含推断过程与数据边界说明。"""

    city: str = Field(min_length=1, max_length=60)
    check_in: date
    check_out: date
    travelers: int = Field(ge=1, le=50)
    nights: int = Field(ge=1, le=365)
    #: 房型建议（按人数推断）
    room_suggestions: list[RoomSuggestion] = Field(default_factory=list)
    hotels: list[Hotel] = Field(default_factory=list)
    #: 数据可得性说明：为什么没有实时价、建议怎么订
    disclaimer: str = Field(default="", max_length=300)
    provider: str = Field(min_length=1)


class TierOption(DomainModel):
    """一个档位的候选。``hotels`` 为空表示该档位在目的地暂无可推荐项。"""

    tier: HotelTier
    #: 中文档位名，如「性价比」
    label: str = Field(min_length=1, max_length=10)
    #: 档位说明
    description: str = Field(default="", max_length=60)
    hotels: list[Hotel] = Field(default_factory=list)
    #: 选档依据的说明，如「按高德星级类目 五星级宾馆」
    basis: str = Field(default="", max_length=80)

    @property
    def available(self) -> bool:
        return bool(self.hotels)


class TieredHotelRecommendation(DomainModel):
    """三档（性价比/轻奢/高奢）推荐结果。

    与 :class:`HotelRecommendation` 的区别：不是一堆平铺酒店，而是
    「每档一个最优解」的结构，更贴合用户「帮我挑一家」的实际决策方式。
    """

    city: str = Field(min_length=1, max_length=60)
    check_in: date
    check_out: date
    travelers: int = Field(ge=1, le=50)
    nights: int = Field(ge=1, le=365)
    room_suggestions: list[RoomSuggestion] = Field(default_factory=list)
    #: 固定三档，按 value -> comfort -> luxury 顺序
    tiers: list[TierOption] = Field(default_factory=list)
    disclaimer: str = Field(default="", max_length=300)
    provider: str = Field(min_length=1)

    def by_tier(self, tier: HotelTier) -> TierOption | None:
        return next((t for t in self.tiers if t.tier == tier), None)
