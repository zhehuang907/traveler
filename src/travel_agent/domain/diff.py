"""行程版本差异（PlanDiff）。

纯函数 ``compute_diff`` 对比新旧 TripPlan，按 (day_index, poi_id 或 title)
匹配条目，产出 added/removed/changed 三类差异。匹配键选 poi_id 保证
事实条目稳定；自由条目（activity/transport/note 无 poi_id）按 title 匹配。
"""

from pydantic import Field

from travel_agent.domain.models import DomainModel
from travel_agent.domain.plan import PlanItem, TripPlan

# 对比的字段（item_id 由水合按序分配，不参与 diff）
_DIFF_FIELDS = (
    "start_time",
    "end_time",
    "duration_min",
    "cost_cny",
    "indoor",
    "weather_adjusted",
    "travel_mode_to_next",
    "notes",
)


class DiffEntry(DomainModel):
    """新增或删除的条目摘要。"""

    day: int = Field(ge=1, le=60)
    item_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=120)


class DiffChange(DomainModel):
    """单个字段的前后变更。"""

    day: int = Field(ge=1, le=60)
    item_id: str = Field(min_length=1)
    field: str = Field(min_length=1, max_length=40)
    before: str = Field(default="")
    after: str = Field(default="")


class PlanDiff(DomainModel):
    """两个行程版本之间的结构化差异。"""

    added: list[DiffEntry] = Field(default_factory=list)
    removed: list[DiffEntry] = Field(default_factory=list)
    changed: list[DiffChange] = Field(default_factory=list)
    reason: str = Field(default="", max_length=500)

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed and not self.changed


def _item_key(item: PlanItem) -> str:
    """条目匹配键：事实条目用 poi_id，自由条目用 title。"""
    return item.poi_id or item.title


def _fmt(value: object) -> str:
    """把字段值格式化为可读字符串（before/after 展示用）。"""
    if value is None:
        return ""
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return str(iso())
    return str(value)


def compute_diff(old: TripPlan, new: TripPlan, reason: str = "") -> PlanDiff:
    """对比两个行程版本，产出结构化差异。

    按 (day_index, item_key) 匹配条目：
    - 仅在新版出现 → added
    - 仅在旧版出现 → removed
    - 两版都有但字段不同 → changed（逐字段列出 before/after）
    """
    old_map: dict[tuple[int, str], PlanItem] = {}
    for day, item in old.iter_items():
        old_map[(day.day_index, _item_key(item))] = item

    new_map: dict[tuple[int, str], PlanItem] = {}
    for day, item in new.iter_items():
        new_map[(day.day_index, _item_key(item))] = item

    added: list[DiffEntry] = []
    removed: list[DiffEntry] = []
    changed: list[DiffChange] = []

    for key, new_item in new_map.items():
        old_item = old_map.get(key)
        if old_item is None:
            added.append(DiffEntry(day=key[0], item_id=new_item.item_id, title=new_item.title))
            continue
        for field in _DIFF_FIELDS:
            old_val = getattr(old_item, field)
            new_val = getattr(new_item, field)
            if old_val != new_val:
                changed.append(
                    DiffChange(
                        day=key[0],
                        item_id=new_item.item_id,
                        field=field,
                        before=_fmt(old_val),
                        after=_fmt(new_val),
                    )
                )

    for key, old_item in old_map.items():
        if key not in new_map:
            removed.append(DiffEntry(day=key[0], item_id=old_item.item_id, title=old_item.title))

    return PlanDiff(added=added, removed=removed, changed=changed, reason=reason)
