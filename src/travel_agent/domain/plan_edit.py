"""存档行程的定向编辑与优化支撑（纯函数，禁止 IO）。

- ``apply_manual_edit``：用户在详情页手改条目后，把编辑合并回当前版本。
  骨架字段（目的地/日期/人数）与事实字段（poi_id/坐标/来源）受保护，
  条目 item_id 统一重编号为 ``d{day}-{seq}``；
- ``rebuild_catalog_from_plan``：把既有行程的事实条目反构为候选目录，
  让优化阶段「LLM 只按编号点菜」的防编造纪律继续生效；
- ``extend_catalog``：把补充检索的新候选并入目录（按 poi_id 去重、编号连续）；
- ``brief_from_plan``：从行程骨架反构需求槽位，供水合对齐天数与日期。
"""

from collections.abc import Mapping, Sequence
from datetime import timedelta

from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.models import Poi
from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan
from travel_agent.domain.plan_builder import (
    CandidateCatalog,
    CandidateEntry,
    CatalogCategory,
    collect_sources,
)

_CATALOG_GROUPS: tuple[CatalogCategory, ...] = ("attraction", "restaurant", "hotel")
_FACT_CATEGORIES: frozenset[str] = frozenset(_CATALOG_GROUPS)


def apply_manual_edit(current: TripPlan, edited: TripPlan) -> TripPlan:
    """把用户编辑合并到当前行程，返回新版本；违反保护规则时抛 ValueError。

    规则：
    - 行程骨架（目的地/起止日期/人数）不可经编辑端点调整，不一致直接拒绝
      （改骨架属于重新规划，应由对话或 AI 优化发生）；
    - 天数与日期必须与骨架一致（day_index 从 1 连续、date 逐日对齐）；
    - 事实条目（attraction/restaurant/hotel）必须携带当前版本中存在的 poi_id，
      坐标与来源强制取当前版本值（客户端不可篡改或伪造）；
    - 自由条目（activity/transport/note）不允许携带坐标或来源（防编造）；
    - budget/summary/tips/条目内容/天备注以编辑值为准；item_id 统一重编号。
    """
    mismatched = _skeleton_mismatch(current, edited)
    if mismatched:
        fields = "、".join(mismatched)
        raise ValueError(f"字段 {fields} 由规划骨架决定，如需调整请重新规划")
    expected_days = (current.end_date - current.start_date).days + 1
    if len(edited.days) != expected_days:
        raise ValueError(f"行程需保留 {expected_days} 天，收到 {len(edited.days)} 天")
    known: dict[str, PlanItem] = {
        item.poi_id: item for _, item in current.iter_items() if item.poi_id is not None
    }
    days: list[PlanDay] = []
    for index, day in enumerate(edited.days):
        day_index = index + 1
        expected_date = current.start_date + timedelta(days=index)
        if day.day_index != day_index:
            raise ValueError(f"第 {day_index} 天的 day_index 必须为 {day_index}")
        if day.date != expected_date:
            raise ValueError(f"第 {day_index} 天日期应为 {expected_date}，收到 {day.date}")
        items = [
            _merge_item(item, day_index, seq, known) for seq, item in enumerate(day.items, start=1)
        ]
        days.append(day.model_copy(update={"items": items}))
    return current.model_copy(
        update={
            "budget_cny": edited.budget_cny,
            "days": days,
            "summary": edited.summary,
            "tips": list(edited.tips),
            "sources": collect_sources(days),
        }
    )


def _skeleton_mismatch(current: TripPlan, edited: TripPlan) -> list[str]:
    """编辑值与骨架不一致的字段名列表（编辑端点不允许改骨架）。"""
    issues: list[str] = []
    if edited.destination != current.destination:
        issues.append("目的地")
    if edited.start_date != current.start_date:
        issues.append("出发日期")
    if edited.end_date != current.end_date:
        issues.append("返程日期")
    if edited.travelers != current.travelers:
        issues.append("出行人数")
    return issues


def _merge_item(
    item: PlanItem, day_index: int, seq: int, known: Mapping[str, PlanItem]
) -> PlanItem:
    """单条编辑合并：重编号 + 事实字段保护（坐标/来源以既有行程为准）。"""
    update: dict[str, object] = {"item_id": f"d{day_index}-{seq}"}
    if item.poi_id is not None:
        origin = known.get(item.poi_id)
        if origin is None:
            raise ValueError(
                f"第 {day_index} 天「{item.title}」引用了未知来源编号，事实条目只能来自既有行程"
            )
        update["location"] = origin.location
        update["sources"] = list(origin.sources)
    elif item.category in _FACT_CATEGORIES:
        raise ValueError(f"第 {day_index} 天「{item.title}」为事实条目但缺少来源编号，无法手动新增")
    else:
        update["location"] = None
        update["sources"] = []
    return item.model_copy(update=update)


def rebuild_catalog_from_plan(plan: TripPlan) -> CandidateCatalog:
    """把既有行程的事实条目反构为候选目录（编号从 1 连续，按类目排序）。

    事实字段（名称/坐标/来源）取自行程快照自身，provider 标记为 snapshot，
    保证优化阶段候选全部真实、可溯源。
    """
    entries: list[CandidateEntry] = []
    seen: set[str] = set()
    for group in _CATALOG_GROUPS:
        for _, item in plan.iter_items():
            if item.category != group or item.poi_id is None or item.location is None:
                continue
            if item.poi_id in seen:
                continue
            seen.add(item.poi_id)
            entries.append(
                CandidateEntry(
                    ref=len(entries) + 1,
                    category=group,
                    poi=Poi(
                        poi_id=item.poi_id,
                        name=item.title,
                        categories=[group],
                        location=item.location,
                        sources=list(item.sources),
                        provider="snapshot",
                    ),
                )
            )
    return CandidateCatalog(entries=tuple(entries))


def extend_catalog(base: CandidateCatalog, extras: Mapping[str, Sequence[Poi]]) -> CandidateCatalog:
    """把补充检索的候选并入目录：按 poi_id 去重，编号接续 base 之后。"""
    entries = list(base.entries)
    seen = {entry.poi.poi_id for entry in entries}
    for group in _CATALOG_GROUPS:
        for poi in extras.get(group, ()):
            if poi.poi_id in seen:
                continue
            seen.add(poi.poi_id)
            entries.append(CandidateEntry(ref=len(entries) + 1, category=group, poi=poi))
    return CandidateCatalog(entries=tuple(entries))


def brief_from_plan(plan: TripPlan) -> TravelBrief:
    """从行程骨架反构需求槽位（优化时供水合对齐天数与日期）。"""
    return TravelBrief(
        destination=plan.destination,
        start_date=plan.start_date,
        end_date=plan.end_date,
        travelers=plan.travelers,
        budget_cny=plan.budget_cny,
    )
