"""TripPlan/PlanItem 模型行为测试。"""

from datetime import date, time

from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan, new_plan_id


def _item(**overrides: object) -> PlanItem:
    base: dict[str, object] = {"item_id": "d1-1", "title": "武侯祠", "category": "attraction"}
    base.update(overrides)
    return PlanItem.model_validate(base)


def test_clock_string_parsed_leniently() -> None:
    item = _item(start_time="09:30", end_time="2026-01-01 18:00")
    assert item.start_time == time(9, 30)
    assert item.end_time is None  # 无法解析时降级 None 而非报错


def test_default_duration_and_cost_none() -> None:
    item = _item()
    assert item.duration_min == 60
    assert item.cost_cny is None
    assert item.sources == []


def test_day_activity_minutes() -> None:
    day = PlanDay(
        day_index=1,
        date=date(2026, 10, 1),
        items=[_item(duration_min=90), _item(item_id="d1-2", duration_min=30)],
    )
    assert day.activity_minutes() == 120


def test_total_cost_rounds_and_iter_items() -> None:
    plan = TripPlan(
        plan_id="x",
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 2),
        travelers=2,
        days=[
            PlanDay(
                day_index=1,
                date=date(2026, 10, 1),
                items=[_item(cost_cny=100.006), _item(item_id="d1-2", title="火锅")],
            )
        ],
    )
    assert plan.total_cost_cny == 100.01
    assert plan.currency == "CNY"
    titles = [item.title for _, item in plan.iter_items()]
    assert titles == ["武侯祠", "火锅"]


def test_per_person_cost_and_serialization_roundtrip() -> None:
    plan = TripPlan(
        plan_id="x",
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 2),
        travelers=4,
        days=[
            PlanDay(
                day_index=1,
                date=date(2026, 10, 1),
                items=[_item(cost_cny=80.0), _item(item_id="d1-2", title="火锅", cost_cny=120.0)],
            )
        ],
    )
    assert plan.per_person_cost_cny == 50.0
    # 默认序列化包含计算字段（前端/SSE 展示依赖）
    payload = plan.model_dump(mode="json")
    assert payload["total_cost_cny"] == 200.0
    assert payload["per_person_cost_cny"] == 50.0
    # 持久化口径：排除计算字段后可无损回读（extra=forbid）
    restored = TripPlan.model_validate_json(
        plan.model_dump_json(exclude_computed_fields=True)
    )
    assert restored.total_cost_cny == 200.0
    assert restored.per_person_cost_cny == 50.0


def test_new_plan_id_is_hex32() -> None:
    plan_id = new_plan_id()
    assert len(plan_id) == 32
    assert all(char in "0123456789abcdef" for char in plan_id)
