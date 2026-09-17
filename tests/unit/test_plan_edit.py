"""plan_edit 纯函数测试：骨架保护、重编号、事实字段防护、目录反构与扩展。"""

from datetime import date

import pytest

from travel_agent.domain.models import GeoPoint, Poi
from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan, new_plan_id
from travel_agent.domain.plan_edit import (
    apply_manual_edit,
    brief_from_plan,
    extend_catalog,
    rebuild_catalog_from_plan,
)

_GEO = GeoPoint(lat=30.6, lng=104.0)


def _item(
    item_id: str,
    title: str,
    category: str = "activity",
    *,
    poi_id: str | None = None,
    location: GeoPoint | None = None,
    sources: list[str] | None = None,
    duration_min: int = 60,
) -> PlanItem:
    return PlanItem(
        item_id=item_id,
        title=title,
        category=category,
        poi_id=poi_id,
        location=location,
        sources=sources or [],
        duration_min=duration_min,
    )


def _base_plan() -> TripPlan:
    return TripPlan(
        plan_id=new_plan_id(),
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 2),
        travelers=2,
        budget_cny=5000,
        days=[
            PlanDay(
                day_index=1,
                date=date(2026, 10, 1),
                items=[
                    _item(
                        "d1-1",
                        "武侯祠",
                        "attraction",
                        poi_id="a1",
                        location=_GEO,
                        sources=["https://example.amap.com/a1"],
                    ),
                    _item("d1-2", "城市漫步", duration_min=120),
                ],
            ),
            PlanDay(
                day_index=2,
                date=date(2026, 10, 2),
                items=[
                    _item(
                        "d2-1",
                        "蜀大侠火锅",
                        "restaurant",
                        poi_id="r1",
                        location=_GEO,
                        sources=["https://example.amap.com/r1"],
                    ),
                ],
            ),
        ],
        summary="原行程",
        tips=["带好肠胃药"],
    )


def _edited_copy(base: TripPlan) -> TripPlan:
    """构造一份可编辑副本（条目重排 + 新增自由条目 + 标题改动）。"""
    return base.model_copy(
        update={
            "summary": "编辑后的行程",
            "days": [
                base.days[0].model_copy(
                    update={
                        "items": [
                            _item(
                                "weird-id-1",
                                "城市漫步（延长）",
                                duration_min=150,
                            ),
                            _item(
                                "weird-id-2",
                                "武侯祠",
                                "attraction",
                                poi_id="a1",
                                location=_GEO,
                                sources=["https://example.amap.com/a1"],
                            ),
                        ]
                    }
                ),
                base.days[1].model_copy(update={"items": []}),
            ],
        }
    )


def test_apply_manual_edit_renumbers_and_preserves_skeleton() -> None:
    base = _base_plan()
    edited = apply_manual_edit(base, _edited_copy(base))
    assert edited.plan_id == base.plan_id
    assert edited.destination == "成都"
    assert edited.summary == "编辑后的行程"
    # item_id 统一重编号且按天连续
    assert [item.item_id for item in edited.days[0].items] == ["d1-1", "d1-2"]
    assert edited.days[1].items == []
    # 事实字段保持原样
    fact = edited.days[0].items[1]
    assert fact.poi_id == "a1"
    assert fact.location == _GEO
    assert [str(url) for url in fact.sources] == ["https://example.amap.com/a1"]
    # sources 汇总随条目变化（r1 被删除后不再出现）
    assert [str(url) for url in edited.sources] == ["https://example.amap.com/a1"]


def test_apply_manual_edit_rejects_skeleton_change() -> None:
    base = _base_plan()
    changed = base.model_copy(update={"travelers": 3})
    with pytest.raises(ValueError, match="出行人数"):
        apply_manual_edit(base, changed)
    changed_date = base.model_copy(update={"start_date": date(2026, 10, 5)})
    with pytest.raises(ValueError, match="出发日期"):
        apply_manual_edit(base, changed_date)


def test_apply_manual_edit_rejects_day_mismatch() -> None:
    base = _base_plan()
    edited = base.model_copy(update={"days": [base.days[0]]})
    with pytest.raises(ValueError, match="2 天"):
        apply_manual_edit(base, edited)


def test_apply_manual_edit_rejects_day_index_or_date_gap() -> None:
    base = _base_plan()
    bad_index = base.model_copy(
        update={"days": [base.days[0].model_copy(update={"day_index": 3}), base.days[1]]}
    )
    with pytest.raises(ValueError, match="day_index"):
        apply_manual_edit(base, bad_index)
    bad_date = base.model_copy(
        update={"days": [base.days[0], base.days[1].model_copy(update={"date": date(2026, 10, 9)})]}
    )
    with pytest.raises(ValueError, match="日期"):
        apply_manual_edit(base, bad_date)


def test_apply_manual_edit_protects_fact_fields() -> None:
    base = _base_plan()
    forged = _item(
        "d1-1",
        "武侯祠",
        "attraction",
        poi_id="a1",
        location=GeoPoint(lat=1.0, lng=2.0),
        sources=["https://evil.example.com/x"],
    )
    edited = base.model_copy(
        update={"days": [base.days[0].model_copy(update={"items": [forged]}), base.days[1]]}
    )
    merged = apply_manual_edit(base, edited)
    item = merged.days[0].items[0]
    # 坐标与来源被强制还原为既有行程的值，篡改无效
    assert item.location == _GEO
    assert [str(url) for url in item.sources] == ["https://example.amap.com/a1"]


def test_apply_manual_edit_rejects_unknown_or_missing_poi() -> None:
    base = _base_plan()
    unknown = _item(
        "x",
        "陌生酒店",
        "hotel",
        poi_id="h9",
        location=_GEO,
        sources=["https://example.amap.com/h9"],
    )
    edited = base.model_copy(
        update={"days": [base.days[0].model_copy(update={"items": [unknown]}), base.days[1]]}
    )
    with pytest.raises(ValueError, match="未知来源编号"):
        apply_manual_edit(base, edited)
    missing = _item("y", "手填景点", "attraction")
    edited2 = base.model_copy(
        update={"days": [base.days[0].model_copy(update={"items": [missing]}), base.days[1]]}
    )
    with pytest.raises(ValueError, match="缺少来源编号"):
        apply_manual_edit(base, edited2)


def test_apply_manual_edit_strips_free_item_location() -> None:
    base = _base_plan()
    smuggled = _item("z", "自由活动", location=_GEO, sources=["https://example.com/note"])
    edited = base.model_copy(
        update={"days": [base.days[0].model_copy(update={"items": [smuggled]}), base.days[1]]}
    )
    merged = apply_manual_edit(base, edited)
    item = merged.days[0].items[0]
    assert item.location is None
    assert item.sources == []


def test_rebuild_catalog_from_plan() -> None:
    catalog = rebuild_catalog_from_plan(_base_plan())
    refs = [(entry.ref, entry.category, entry.poi.name) for entry in catalog.entries]
    # 类目顺序：attraction -> restaurant -> hotel；编号从 1 连续
    assert refs == [(1, "attraction", "武侯祠"), (2, "restaurant", "蜀大侠火锅")]
    assert catalog.entries[0].poi.provider == "snapshot"
    assert catalog.business_hours() == {}


def test_rebuild_catalog_skips_duplicates_and_free_items() -> None:
    base = _base_plan()
    duplicated = base.model_copy(
        update={
            "days": [
                base.days[0],
                base.days[1].model_copy(
                    update={
                        "items": [
                            base.days[1].items[0],
                            _item("d2-2", "无坐标事实条目", "hotel", poi_id="h1"),
                        ]
                    }
                ),
            ]
        }
    )
    catalog = rebuild_catalog_from_plan(duplicated)
    names = [entry.poi.name for entry in catalog.entries]
    # 重复 poi_id 只保留一条；无坐标事实条目被跳过；自由条目不进目录
    assert names == ["武侯祠", "蜀大侠火锅"]


def test_extend_catalog_dedupes_and_continues_numbering() -> None:
    base = rebuild_catalog_from_plan(_base_plan())
    extra = Poi(
        poi_id="h1",
        name="春熙路商务酒店",
        location=_GEO,
        sources=["https://example.amap.com/h1"],
        provider="amap",
    )
    duplicated = Poi(
        poi_id="a1",
        name="武侯祠",
        location=_GEO,
        provider="amap",
    )
    merged = extend_catalog(base, {"hotel": [extra, duplicated]})
    refs = [(entry.ref, entry.category, entry.poi.name) for entry in merged.entries]
    assert refs == [
        (1, "attraction", "武侯祠"),
        (2, "restaurant", "蜀大侠火锅"),
        (3, "hotel", "春熙路商务酒店"),
    ]


def test_brief_from_plan() -> None:
    brief = brief_from_plan(_base_plan())
    assert brief.destination == "成都"
    assert brief.start_date == date(2026, 10, 1)
    assert brief.end_date == date(2026, 10, 2)
    assert brief.travelers == 2
    assert brief.budget_cny == 5000
    assert brief.duration_days == 2
