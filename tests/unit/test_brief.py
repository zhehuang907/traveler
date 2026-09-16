"""TravelBrief 槽位模型测试。"""

from datetime import date

import pytest
from pydantic import ValidationError

from travel_agent.domain.brief import TravelBrief


def test_default_brief_all_required_missing() -> None:
    brief = TravelBrief()
    assert brief.missing_slots() == [
        "destination",
        "start_date",
        "end_date",
        "travelers",
        "budget_cny",
        "pace",
    ]
    assert "目的地" in brief.missing_slot_labels()
    assert not brief.is_ready()
    assert brief.duration_days is None
    assert brief.pace_label is None


def test_ready_brief_and_duration_inclusive() -> None:
    brief = TravelBrief(
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 4),
        travelers=2,
        budget_cny=5000,
        pace="moderate",
    )
    assert brief.missing_slots() == []
    assert brief.is_ready()
    assert brief.duration_days == 4
    assert brief.pace_label == "适中"


def test_empty_destination_counts_as_missing() -> None:
    brief = TravelBrief(destination="   ")
    assert "destination" in brief.missing_slots()


def test_inverted_date_range_not_ready() -> None:
    brief = TravelBrief(
        destination="成都",
        start_date=date(2026, 10, 5),
        end_date=date(2026, 10, 1),
        travelers=1,
        budget_cny=1000,
        pace="relaxed",
    )
    assert not brief.is_ready()


def test_travelers_bounds() -> None:
    with pytest.raises(ValidationError):
        TravelBrief(travelers=0)
    with pytest.raises(ValidationError):
        TravelBrief(travelers=51)


def test_optional_lists_default_independent() -> None:
    first = TravelBrief()
    second = TravelBrief()
    assert first.preferences == []
    first.preferences.append("博物馆")
    assert second.preferences == []
