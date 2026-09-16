"""LLM 行程草稿的结构化契约（纯数据，内核可引用）。

LLM 不直接输出最终 TripPlan，而是输出「按候选编号点菜」的草稿：
事实字段（名称/坐标/来源链接）一律由 plan_builder 按编号水合，
从机制上杜绝编造景点与 URL。
"""

from datetime import date

from pydantic import Field

from travel_agent.domain.models import DomainModel, TravelMode
from travel_agent.domain.plan import ItemCategory


class DraftItem(DomainModel):
    """单条草稿。``candidate_ref`` 指向候选目录编号（从 1 开始）。"""

    title: str = Field(min_length=1, max_length=120)
    category: ItemCategory
    candidate_ref: int | None = Field(default=None, ge=1)
    start_clock: str = Field(default="", max_length=5)
    end_clock: str = Field(default="", max_length=5)
    duration_min: int = Field(default=60, ge=5, le=600)
    cost_cny: float | None = Field(default=None, ge=0.0)
    indoor: bool = False
    travel_mode_to_next: TravelMode | None = None
    notes: str = Field(default="", max_length=500)


class DraftDay(DomainModel):
    """一天草稿。"""

    date: date
    items: list[DraftItem] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=500)


class PlanDraft(DomainModel):
    """compose/revise 节点的结构化输出形态。"""

    days: list[DraftDay] = Field(min_length=1, max_length=60)
    summary: str = Field(default="", max_length=2_000)
    tips: list[str] = Field(default_factory=list, max_length=10)
