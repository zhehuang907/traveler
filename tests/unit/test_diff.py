"""compute_diff 纯函数单测：added/removed/changed/empty 全分支。"""

from datetime import date, time

from travel_agent.domain.diff import DiffEntry, PlanDiff, compute_diff
from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan, new_plan_id


def _item(
    *,
    item_id: str = "d1-1",
    title: str = "武侯祠",
    category: str = "attraction",
    poi_id: str | None = "a1",
    start: str = "09:00",
    end: str = "11:00",
    duration: int = 120,
    cost: float = 50,
) -> PlanItem:
    return PlanItem(
        item_id=item_id,
        title=title,
        category=category,
        poi_id=poi_id,
        start_time=time.fromisoformat(start) if start else None,
        end_time=time.fromisoformat(end) if end else None,
        duration_min=duration,
        cost_cny=cost,
    )


def _plan(items_by_day: list[list[PlanItem]], *, plan_id: str | None = None) -> TripPlan:
    days = [
        PlanDay(day_index=i + 1, date=date(2026, 10, 1 + i), items=items)
        for i, items in enumerate(items_by_day)
    ]
    return TripPlan(
        plan_id=plan_id or new_plan_id(),
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, len(items_by_day)),
        travelers=2,
        days=days,
    )


def test_diff_identical_plans_is_empty() -> None:
    old = _plan([[_item()]])
    new = _plan([[_item()]], plan_id=old.plan_id)
    diff = compute_diff(old, new)
    assert diff.is_empty
    assert diff.added == []
    assert diff.removed == []
    assert diff.changed == []


def test_diff_added_item() -> None:
    old = _plan([[_item(item_id="d1-1")]])
    new = _plan(
        [
            [
                _item(item_id="d1-1"),
                _item(item_id="d1-2", title="锦里", poi_id="a2"),
            ]
        ],
        plan_id=old.plan_id,
    )
    diff = compute_diff(old, new, reason="增加一个景点")
    assert len(diff.added) == 1
    assert diff.added[0].day == 1
    assert diff.added[0].title == "锦里"
    assert diff.reason == "增加一个景点"
    assert not diff.removed
    assert not diff.changed


def test_diff_removed_item() -> None:
    old = _plan([[_item(item_id="d1-1"), _item(item_id="d1-2", title="锦里", poi_id="a2")]])
    new = _plan([[_item(item_id="d1-1")]], plan_id=old.plan_id)
    diff = compute_diff(old, new)
    assert len(diff.removed) == 1
    assert diff.removed[0].title == "锦里"
    assert not diff.added


def test_diff_changed_field() -> None:
    old = _plan([[_item(start="09:00", cost=50)]])
    new = _plan([[_item(start="10:00", cost=80)]], plan_id=old.plan_id)
    diff = compute_diff(old, new)
    assert not diff.added
    assert not diff.removed
    fields = {c.field for c in diff.changed}
    assert "start_time" in fields
    assert "cost_cny" in fields
    # before/after 值可读
    start_change = next(c for c in diff.changed if c.field == "start_time")
    assert start_change.before == "09:00:00"
    assert start_change.after == "10:00:00"


def test_diff_free_items_match_by_title() -> None:
    """自由条目（activity/transport/note 无 poi_id）按 title 匹配。"""
    item = _item(
        item_id="d1-3",
        title="城市漫步",
        category="activity",
        poi_id=None,
        duration=60,
    )
    old = _plan([[_item(), item]])
    # 同 title 不同 duration → changed
    modified = item.model_copy(update={"duration_min": 90})
    new = _plan([[_item(), modified]], plan_id=old.plan_id)
    diff = compute_diff(old, new)
    assert not diff.added
    assert not diff.removed
    assert any(c.field == "duration_min" for c in diff.changed)


def test_diff_across_days() -> None:
    """条目从第 1 天移到第 2 天 → removed (d1) + added (d2)。"""
    old = _plan(
        [
            [_item(item_id="d1-1"), _item(item_id="d1-2", title="锦里", poi_id="a2")],
            [],
        ]
    )
    new = _plan(
        [
            [_item(item_id="d1-1")],
            [_item(item_id="d2-1", title="锦里", poi_id="a2")],
        ],
        plan_id=old.plan_id,
    )
    diff = compute_diff(old, new)
    assert len(diff.removed) == 1
    assert diff.removed[0].day == 1
    assert len(diff.added) == 1
    assert diff.added[0].day == 2


def test_plandiff_is_empty_property() -> None:
    empty = PlanDiff()
    assert empty.is_empty
    non_empty = PlanDiff(added=[DiffEntry(day=1, item_id="d1-1", title="x")])
    assert not non_empty.is_empty
