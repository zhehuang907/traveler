"""领域模型：外部世界信息进入 Agent 前的统一形态。

本模块是纯内核：禁止 import services/agent，禁止任何 IO。
所有 Provider 返回的裸 dict 必须先映射成本处的 Pydantic 模型，
层间传递不允许出现未经校验的 dict。
"""

from datetime import date, time
from typing import Literal

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
