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


def _ready_brief() -> TravelBrief:
    return TravelBrief(
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 4),
        travelers=2,
        budget_cny=5000,
        pace="moderate",
    )


def test_ready_brief_without_pace_is_ready() -> None:
    """节奏缺失不再阻塞规划（编排按适中兜底），仅时间/人数/预算/目的地必需。"""
    brief = TravelBrief(
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 4),
        travelers=2,
        budget_cny=5000,
    )
    assert brief.missing_slots() == []
    assert brief.is_ready()
    assert brief.pace is None  # 未指定节奏 → 不追问，编排兜底


def test_transport_guide_self_driving_mentions_parking() -> None:
    guide = _ready_brief().model_copy(update={"transport": "self_driving"})
    text = guide.transport_guide_text()
    assert "自驾" in text
    assert "方便停车" in text
    assert "停车建议" in text


def test_transport_guide_public_default() -> None:
    # 未指定出行方式 → 默认公共交通编排
    text = _ready_brief().transport_guide_text()
    assert "公共交通" in text
    assert "地铁" in text
    assert "步行" not in text


def test_merge_keeps_existing_slots_when_incoming_empty() -> None:
    """LLM 抽取结果缺槽位时不覆盖已有值（防下一轮重复追问）。"""
    existing = _ready_brief().model_copy(
        update={"guide_ready": True, "must_visit": ["武侯祠"], "transport": "public"}
    )
    # 新抽取结果几乎全空（仅 destination 不同），模拟 LLM 结构化输出置空
    incoming = TravelBrief(destination="南京")
    merged = existing.merge(incoming)
    assert merged.destination == "南京"  # 新值覆盖
    assert merged.start_date == date(2026, 10, 1)  # 旧值保留
    assert merged.end_date == date(2026, 10, 4)
    assert merged.travelers == 2
    assert merged.budget_cny == 5000
    assert merged.pace == "moderate"
    assert merged.guide_ready is True
    assert merged.must_visit == ["武侯祠"]
    assert merged.transport == "public"


def test_merge_updates_new_and_dedupes_lists() -> None:
    existing = _ready_brief().model_copy(update={"must_visit": ["武侯祠"]})
    incoming = TravelBrief(
        travelers=4,
        budget_cny=8000,
        pace="packed",
        must_visit=["武侯祠", "大熊猫基地"],
        dietary=["辣"],
    )
    merged = existing.merge(incoming)
    assert merged.travelers == 4
    assert merged.budget_cny == 8000
    assert merged.pace == "packed"
    assert merged.must_visit == ["武侯祠", "大熊猫基地"]  # 去重合并
    assert merged.dietary == ["辣"]
    # 未提及字段保持原值
    assert merged.destination == "成都"


def test_summary_text_lists_confirmed_info() -> None:
    summary = _ready_brief().model_copy(update={"transport": "public"}).summary_text()
    assert "目的地 成都" in summary
    assert "2026-10-01 至 2026-10-04" in summary
    assert "2人" in summary
    assert "预算 5000元" in summary
    assert "节奏适中" in summary
    assert "公共交通" in summary
    assert TravelBrief().summary_text() == "（暂无）"
