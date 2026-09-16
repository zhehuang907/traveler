"""行程单领域模型（Agent 的最终产物）。

与外部世界模型分离：PlanItem 中每条事实（名称/坐标/来源/营业时间）
都由 plan_builder 从候选 Poi 水合而来，LLM 只决定「选哪个候选、怎么排」。
"""

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import Field, HttpUrl, computed_field, field_validator

from travel_agent.domain.models import DomainModel, GeoPoint, TravelMode

ItemCategory = Literal["attraction", "restaurant", "hotel", "activity", "transport", "note"]


class PlanItem(DomainModel):
    """行程中的一个条目（景点/餐饮/住宿/活动/交通/备注）。"""

    item_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=120)
    category: ItemCategory
    poi_id: str | None = None
    location: GeoPoint | None = None
    start_time: time | None = None
    end_time: time | None = None
    duration_min: int = Field(default=60, ge=0, le=1_440)
    cost_cny: float | None = Field(default=None, ge=0.0)
    indoor: bool = False
    # True 表示该条目是因当日高降水概率而改排的室内方案
    weather_adjusted: bool = False
    travel_mode_to_next: TravelMode | None = None
    notes: str = Field(default="", max_length=500)
    sources: list[HttpUrl] = Field(default_factory=list)

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _parse_clock(cls, value: object) -> object:
        """容忍 LLM 输出的 ``"09:30"`` 文本；无法解析时降级为 None 而非报错。"""
        if not isinstance(value, str) or not value.strip():
            return value
        text = value.strip()
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).time()
            except ValueError:
                continue
        return None


class PlanDay(DomainModel):
    """一天的安排（按时间顺序的条目序列）。"""

    day_index: int = Field(ge=1, le=60)
    date: date
    items: list[PlanItem] = Field(default_factory=list)
    note: str = Field(default="", max_length=500)

    def activity_minutes(self) -> int:
        """当天条目停留时长合计（不含通勤，通勤由校验节点核实）。"""
        return sum(item.duration_min for item in self.items)


class TripPlan(DomainModel):
    """完整行程单。不可变；修订时整体替换新版本。"""

    plan_id: str = Field(min_length=1)
    destination: str = Field(min_length=1, max_length=60)
    city_location: GeoPoint | None = None
    start_date: date
    end_date: date
    travelers: int = Field(ge=1, le=50)
    budget_cny: float | None = Field(default=None, ge=0.0)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    days: list[PlanDay] = Field(default_factory=list)
    summary: str = Field(default="", max_length=2_000)
    tips: list[str] = Field(default_factory=list)
    sources: list[HttpUrl] = Field(default_factory=list)
    # True 表示部分日期使用了历史同期气候参考而非实时预报
    climate_reference: bool = False

    # 计算字段：会进入所有序列化出口；持久化/回读路径（DB 快照、checkpoint、分享）
    # 必须 exclude_computed_fields=True，否则 extra=forbid 会拒绝回读
    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_cost_cny(self) -> float:
        """全部条目花费合计（无花费信息的条目按 0 计）。"""
        return round(
            sum(
                item.cost_cny
                for day in self.days
                for item in day.items
                if item.cost_cny is not None
            ),
            2,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def per_person_cost_cny(self) -> float:
        """人均花费参考值（总花费 / 出行人数，近似值）。"""
        return round(self.total_cost_cny / self.travelers, 2)

    def iter_items(self) -> tuple[tuple[PlanDay, PlanItem], ...]:
        return tuple((day, item) for day in self.days for item in day.items)


def new_plan_id() -> str:
    """行程单 ID（uuid4 hex，无敏感信息）。"""
    return uuid.uuid4().hex
