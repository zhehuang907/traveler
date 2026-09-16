"""domain/rules.py 全分支测试（要求 100% 覆盖）。"""

from datetime import date, time

from travel_agent.domain.models import DailyWeather
from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan
from travel_agent.domain.rules import (
    check_budget,
    check_commute_gap,
    check_day_load,
    check_empty_day,
    check_rainy_indoor,
    check_visit_window,
    evaluate_plan,
    extreme_weather_alerts,
    gap_minutes,
    parse_open_window,
)

D1 = date(2026, 10, 1)
D2 = date(2026, 10, 2)


def _item(item_id: str = "d1-1", **overrides: object) -> PlanItem:
    base: dict[str, object] = {
        "item_id": item_id,
        "title": f"地点{item_id}",
        "category": "attraction",
        "duration_min": 60,
    }
    base.update(overrides)
    return PlanItem.model_validate(base)


def _day(day_index: int = 1, day_date: date = D1, items: list[PlanItem] | None = None) -> PlanDay:
    return PlanDay(day_index=day_index, date=day_date, items=items if items is not None else [])


def _plan(days: list[PlanDay], budget: float | None = 10_000.0) -> TripPlan:
    return TripPlan(
        plan_id="p1",
        destination="成都",
        start_date=days[0].date,
        end_date=days[-1].date,
        travelers=2,
        budget_cny=budget,
        days=days,
    )


def _weather(
    day: date = D1,
    *,
    precip_prob: int | None = 90,
    temp_min: float = 15.0,
    temp_max: float = 22.0,
    condition: str = "小雨",
) -> DailyWeather:
    return DailyWeather(
        date=day,
        temp_min=temp_min,
        temp_max=temp_max,
        condition=condition,
        precip_prob=precip_prob,
    )


# ---------- check_day_load ----------


def test_day_load_over_limit_and_within() -> None:
    day = _day(items=[_item(duration_min=300), _item(duration_min=300)])
    over = check_day_load(day, commute_minutes=120, max_hours=8)
    assert over is not None and "超出每日上限" in over
    assert check_day_load(day, commute_minutes=-5, max_hours=24) is None


# ---------- check_empty_day ----------


def test_empty_day_detection() -> None:
    assert "没有任何安排" in (check_empty_day(_day()) or "")
    assert check_empty_day(_day(items=[_item()])) is None


# ---------- check_budget ----------


def test_budget_checks() -> None:
    cheap = _plan([_day(items=[_item(cost_cny=100)])], budget=500)
    assert check_budget(cheap) is None
    pricey = _plan([_day(items=[_item(cost_cny=600)])], budget=500)
    over = check_budget(pricey)
    assert over is not None and "超出预算" in over
    no_budget = _plan([_day(items=[_item(cost_cny=600)])], budget=None)
    assert check_budget(no_budget) is None


# ---------- check_rainy_indoor ----------


def test_rainy_indoor_rules() -> None:
    day = _day(items=[_item()])
    assert check_rainy_indoor(day, None) is None
    assert check_rainy_indoor(day, _weather(precip_prob=30)) is None
    issue = check_rainy_indoor(day, _weather())
    assert issue is not None and "室内备选" in issue
    indoor_day = _day(items=[_item(indoor=True)])
    assert check_rainy_indoor(indoor_day, _weather()) is None
    adjusted_day = _day(items=[_item(weather_adjusted=True)])
    assert check_rainy_indoor(adjusted_day, _weather()) is None


# ---------- parse_open_window ----------


def test_parse_open_window_variants() -> None:
    assert parse_open_window("全天开放") is None
    parsed = parse_open_window("09:00-18:00")
    assert parsed == (time(9, 0), time(18, 0))
    assert parse_open_window("9:30~22:00") is not None
    assert parse_open_window("营业时间 09:00 至 21:30") is not None
    assert parse_open_window("25:00-28:00") is None


# ---------- check_visit_window ----------


def test_visit_window_checks() -> None:
    item = _item(poi_id="a1", start_time="09:00", end_time="10:00")
    assert check_visit_window(item, None) is None
    no_clock = _item(poi_id="a1")
    assert check_visit_window(no_clock, "09:00-18:00") is None
    assert check_visit_window(item, "全天开放") is None
    assert check_visit_window(item, "09:00-18:00") is None
    early = _item(poi_id="a1", start_time="08:00", end_time="09:30")
    issue = check_visit_window(early, "09:00-18:00")
    assert issue is not None and "营业时段" in issue
    late = _item(poi_id="a1", start_time="17:00", end_time="19:00")
    assert check_visit_window(late, "09:00-18:00") is not None


# ---------- commute ----------


def test_gap_minutes_negative_and_missing() -> None:
    earlier = _item(end_time="10:00")
    later = _item(item_id="d1-2", start_time="10:30")
    assert gap_minutes(earlier, later) == 30
    assert gap_minutes(_item(), _item(item_id="d1-2")) is None
    crossed = _item(end_time="11:00")
    before = _item(item_id="d1-2", start_time="10:00")
    assert gap_minutes(crossed, before) is None


def test_check_commute_gap() -> None:
    assert check_commute_gap(60, None) is None
    assert check_commute_gap(60, 10) is not None
    assert check_commute_gap(25, 10) is None  # 恰好空档+15 缓冲，不判违规


# ---------- extreme weather ----------


def test_extreme_weather_alerts() -> None:
    alerts = extreme_weather_alerts(
        {
            D1: _weather(D1, temp_max=38.0, precip_prob=None, condition="晴"),
            D2: _weather(D2, temp_min=-1.0, precip_prob=None, condition="多云"),
        }
    )
    assert any("高温" in text for text in alerts)
    assert any("保暖" in text for text in alerts)
    storm = extreme_weather_alerts(
        {D1: _weather(D1, temp_min=10, temp_max=20, precip_prob=90, condition="暴雨")}
    )
    assert any("暴雨" in text for text in storm)
    assert extreme_weather_alerts({D1: _weather(D1, precip_prob=None)}) == []


# ---------- evaluate_plan 集成 ----------


def test_evaluate_plan_empty_day_short_circuits() -> None:
    reflections = evaluate_plan(_plan([_day()]), {}, {}, {}, 8)
    assert reflections == ["第 1 天没有任何安排，需要补足或删除该天"]


def test_evaluate_plan_full_issues() -> None:
    rainy_day = _day(items=[_item(cost_cny=100)])
    plan = _plan([rainy_day], budget=50)
    weather = {D1: _weather()}
    reflections = evaluate_plan(plan, weather, {}, {}, 8)
    assert any("室内备选" in text for text in reflections)
    assert any("超出预算" in text for text in reflections)


def test_evaluate_plan_visit_and_commute_conflicts() -> None:
    items = [
        _item("d1-1", poi_id="a1", start_time="08:00", end_time="09:00"),
        _item("d1-2", poi_id="a2", start_time="09:10", end_time="10:00"),
        _item("d1-3", poi_id="a3", start_time="11:00", end_time="12:00"),
        _item("d1-4", poi_id="a4", start_time="13:00", end_time="14:00"),
    ]
    day = _day(items=items)
    plan = _plan([day])
    hours = {"a1": "09:00-18:00"}
    # d1-3→d1-4 不在映射中：该对直接跳过
    commutes = {("d1-1", "d1-2"): 60, ("d1-2", "d1-3"): 20}
    reflections = evaluate_plan(plan, {}, hours, commutes, 8)
    assert any("营业时段" in text for text in reflections)
    assert any("通勤" in text for text in reflections)


def test_evaluate_plan_day_load_via_commute_sum_and_cross_day_pair() -> None:
    day1 = _day(1, D1, [_item("d1-1"), _item("d1-2", duration_min=60)])
    day2 = _day(2, D2, [_item("d2-1")])
    plan = _plan([day1, day2], budget=None)
    # 同天对：大通勤推高总时长（条目无时刻，单对空档校验跳过，但合计仍计入）
    # 跨天对：不应计入任何一天
    commutes = {("d1-1", "d1-2"): 600, ("d1-1", "d2-1"): 999}
    reflections = evaluate_plan(plan, {}, {}, commutes, max_daily_hours=2)
    assert any("超出每日上限" in text for text in reflections)
    assert not any("d2-1" in text for text in reflections)


def test_evaluate_plan_clean() -> None:
    items = [
        _item("d1-1", poi_id="a1", start_time="09:00", end_time="11:00", cost_cny=80),
        _item("d1-2", poi_id="a2", start_time="12:00", end_time="13:00", cost_cny=80),
    ]
    plan = _plan([_day(items=items)], budget=500)
    commutes = {("d1-1", "d1-2"): 20}
    assert evaluate_plan(plan, {}, {"a1": "08:00-20:00", "a2": "10:00-22:00"}, commutes, 8) == []
