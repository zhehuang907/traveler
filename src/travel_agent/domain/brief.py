"""结构化出行需求（槽位）。

由意图识别节点从自然语言中抽取；相对日期（「下个月」）必须在 LLM 侧
结合今天解析成绝对日期后填入，内核不做自然语言时间解析。
"""

from datetime import date
from typing import ClassVar, Literal

from pydantic import Field, field_validator

from travel_agent.domain.models import DomainModel

Pace = Literal["relaxed", "moderate", "packed"]

_PACE_LABEL = {"relaxed": "轻松", "moderate": "适中", "packed": "紧凑"}


class TravelBrief(DomainModel):
    """一次规划请求的结构化槽位。"""

    destination: str = Field(default="", max_length=60)
    start_date: date | None = None
    end_date: date | None = None
    travelers: int | None = Field(default=None, ge=1, le=50)
    budget_cny: float | None = Field(default=None, ge=0.0)
    pace: Pace | None = None
    # 偏好/忌口/必去/必避允许为空：空不触发追问
    preferences: list[str] = Field(default_factory=list)
    dietary: list[str] = Field(default_factory=list)
    must_visit: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)

    @field_validator("destination", mode="before")
    @classmethod
    def _strip_destination(cls, value: object) -> object:
        """纯空白目的地归一为空串，使必填槽位判定能识别为缺失。"""
        return value.strip() if isinstance(value, str) else value

    _REQUIRED_SLOTS: ClassVar[tuple[str, ...]] = (
        "destination",
        "start_date",
        "end_date",
        "travelers",
        "budget_cny",
        "pace",
    )
    _SLOT_LABELS: ClassVar[dict[str, str]] = {
        "destination": "目的地",
        "start_date": "出发日期",
        "end_date": "返程日期",
        "travelers": "出行人数",
        "budget_cny": "预算（人民币）",
        "pace": "节奏偏好（轻松/适中/紧凑）",
    }

    def missing_slots(self) -> list[str]:
        """返回仍需追问的必填槽位字段名。"""
        return [name for name in self._REQUIRED_SLOTS if getattr(self, name) in (None, "")]

    def missing_slot_labels(self) -> list[str]:
        """缺失槽位的中文说明（供聚合追问一次性列出）。"""
        return [self._SLOT_LABELS[name] for name in self.missing_slots()]

    def is_ready(self) -> bool:
        """必填槽位齐备且日期区间合法，才进入检索编排。"""
        if self.missing_slots():
            return False
        return (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date <= self.end_date
        )

    @property
    def duration_days(self) -> int | None:
        """行程天数（含首尾）；日期缺失时为 None。"""
        if self.start_date is None or self.end_date is None:
            return None
        return (self.end_date - self.start_date).days + 1

    @property
    def pace_label(self) -> str | None:
        return _PACE_LABEL.get(self.pace) if self.pace else None
