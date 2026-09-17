"""候选目录与草稿→行程单的水合（纯函数，禁止 IO）。

水合规则（防编造的机制保证）：
- attraction/restaurant/hotel 条目必须带有效候选编号，事实字段全部取自候选 Poi；
- activity/transport/note 可以无编号（城市漫步、交通衔接、备注），但不得带坐标/链接；
- 来源链接只可能来自候选 Poi，LLM 不提供任何 URL；
- 雨天标记、日期对齐、ID 分配、来源去重全部在此确定性完成。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from pydantic import HttpUrl

from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.draft import DraftDay, DraftItem, PlanDraft
from travel_agent.domain.models import DailyWeather, GeoPoint, Poi
from travel_agent.domain.plan import (
    PlanDay,
    PlanItem,
    TripPlan,
    new_plan_id,
)

CatalogCategory = Literal["attraction", "restaurant", "hotel"]
# 必须有候选编号才允许进入行程的类目（事实型条目）
_FACT_CATEGORIES: frozenset[str] = frozenset({"attraction", "restaurant", "hotel"})


@dataclass(frozen=True)
class CandidateEntry:
    """候选目录中的一条 POI（编号对 LLM 可见，从 1 开始）。"""

    ref: int
    category: CatalogCategory
    poi: Poi


@dataclass(frozen=True)
class CandidateCatalog:
    """检索阶段产出的全部候选（按类目分组，编号唯一）。"""

    entries: tuple[CandidateEntry, ...]

    def by_ref(self) -> dict[int, CandidateEntry]:
        return {entry.ref: entry for entry in self.entries}

    def business_hours(self) -> dict[str, str]:
        """poi_id -> 营业时间文本（校验节点使用）。"""
        return {
            entry.poi.poi_id: entry.poi.business_hours
            for entry in self.entries
            if entry.poi.business_hours
        }

    @classmethod
    def from_groups(
        cls,
        attractions: Sequence[Poi],
        restaurants: Sequence[Poi],
        hotels: Sequence[Poi],
    ) -> "CandidateCatalog":
        entries: list[CandidateEntry] = []
        ref = 1
        groups: tuple[tuple[CatalogCategory, Sequence[Poi]], ...] = (
            ("attraction", attractions),
            ("restaurant", restaurants),
            ("hotel", hotels),
        )
        for category, pois in groups:
            for poi in pois:
                entries.append(CandidateEntry(ref=ref, category=category, poi=poi))
                ref += 1
        return cls(entries=tuple(entries))


@dataclass(frozen=True)
class HydrationResult:
    plan: TripPlan
    warnings: tuple[str, ...]
    dropped: tuple[str, ...]


def hydrate_plan(
    draft: PlanDraft,
    brief: TravelBrief,
    catalog: CandidateCatalog,
    weather: Mapping[date, DailyWeather],
    city_location: GeoPoint | None,
) -> HydrationResult:
    """把 LLM 草稿按候选目录水合成可校验的 TripPlan。"""
    warnings: list[str] = []
    dropped: list[str] = []
    refs = catalog.by_ref()
    expected_days = brief.duration_days or len(draft.days)
    days: list[PlanDay] = []
    for index, draft_day in enumerate(draft.days[:expected_days]):
        day_date = _dated(brief, index)
        day_items, day_dropped = _hydrate_day(draft_day, index + 1, day_date, refs, weather)
        dropped.extend(day_dropped)
        days.append(
            PlanDay(day_index=index + 1, date=day_date, items=day_items, note=draft_day.note)
        )
    if len(draft.days) > expected_days:
        warnings.append(f"草稿多出 {len(draft.days) - expected_days} 天，已按需求天数截断")
    if len(days) < expected_days:
        warnings.append(f"草稿仅覆盖 {len(days)}/{expected_days} 天，缺少日期需补齐")
    if dropped:
        warnings.append(f"{len(dropped)} 个无候选来源的条目已剔除：{'、'.join(dropped)}")
    sources = collect_sources(days)
    plan = TripPlan(
        plan_id=new_plan_id(),
        destination=brief.destination,
        city_location=city_location,
        start_date=brief.start_date or days[0].date,
        end_date=brief.end_date or days[-1].date,
        travelers=brief.travelers or 1,
        budget_cny=brief.budget_cny,
        days=days,
        summary=draft.summary,
        tips=list(draft.tips),
        sources=sources,
        climate_reference=any(daily.climate for daily in weather.values()),
    )
    return HydrationResult(plan=plan, warnings=tuple(warnings), dropped=tuple(dropped))


def _dated(brief: TravelBrief, index: int) -> date:
    if brief.start_date is None:  # is_ready() 已保证，水合只在编排阶段调用
        raise ValueError("水合行程要求 brief.start_date 齐备")
    return brief.start_date + timedelta(days=index)


def _hydrate_day(
    draft_day: DraftDay,
    day_index: int,
    day_date: date,
    refs: Mapping[int, CandidateEntry],
    weather: Mapping[date, DailyWeather],
) -> tuple[list[PlanItem], list[str]]:
    items: list[PlanItem] = []
    dropped: list[str] = []
    daily = weather.get(day_date)
    rainy = daily is not None and daily.rainy
    for seq, draft_item in enumerate(draft_day.items, start=1):
        hydrated = _hydrate_item(draft_item, day_index, seq, refs, rainy)
        if hydrated is None:
            dropped.append(draft_item.title)
            continue
        items.append(hydrated)
    return items, dropped


def _hydrate_item(
    draft_item: DraftItem,
    day_index: int,
    seq: int,
    refs: Mapping[int, CandidateEntry],
    rainy: bool,
) -> PlanItem | None:
    entry = refs.get(draft_item.candidate_ref) if draft_item.candidate_ref else None
    if draft_item.category in _FACT_CATEGORIES and entry is None:
        return None
    if entry is not None:
        return _from_candidate(draft_item, day_index, seq, entry, rainy)
    return _free_item(draft_item, day_index, seq, rainy)


def _from_candidate(
    draft_item: DraftItem,
    day_index: int,
    seq: int,
    entry: CandidateEntry,
    rainy: bool,
) -> PlanItem:
    poi = entry.poi
    return PlanItem(
        item_id=f"d{day_index}-{seq}",
        title=poi.name,
        category=entry.category,
        poi_id=poi.poi_id,
        location=poi.location,
        start_time=draft_item.start_clock or None,
        end_time=draft_item.end_clock or None,
        duration_min=draft_item.duration_min,
        cost_cny=draft_item.cost_cny,
        indoor=draft_item.indoor,
        weather_adjusted=rainy and draft_item.indoor,
        travel_mode_to_next=draft_item.travel_mode_to_next,
        notes=draft_item.notes,
        sources=list(poi.sources),
    )


def _free_item(draft_item: DraftItem, day_index: int, seq: int, rainy: bool) -> PlanItem:
    """非事实型条目（活动/交通/备注）：不允许携带坐标或来源。"""
    return PlanItem(
        item_id=f"d{day_index}-{seq}",
        title=draft_item.title,
        category=draft_item.category,
        start_time=draft_item.start_clock or None,
        end_time=draft_item.end_clock or None,
        duration_min=draft_item.duration_min,
        cost_cny=draft_item.cost_cny,
        indoor=draft_item.indoor,
        weather_adjusted=rainy and draft_item.indoor,
        travel_mode_to_next=draft_item.travel_mode_to_next,
        notes=draft_item.notes,
    )


def collect_sources(days: Sequence[PlanDay]) -> list[HttpUrl]:
    """按顺序去重聚合全部条目的来源链接（行程快照的 sources 字段）。"""
    seen: set[str] = set()
    urls: list[HttpUrl] = []
    for day in days:
        for item in day.items:
            for url in item.sources:
                key = str(url)
                if key not in seen:
                    seen.add(key)
                    urls.append(url)
    return urls
