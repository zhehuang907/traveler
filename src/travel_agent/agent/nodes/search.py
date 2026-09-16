"""检索节点：按 brief 构造查询，asyncio.gather 并行扇出（信号量限流）。

POI/酒店/提示/攻略全部并发，工具层保证「失败不炸断」，这里只负责
去重、分组、限量，并把 trace 快照带回状态。
"""

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass

from travel_agent.agent.state import CandidateGroups, TravelState
from travel_agent.agent.tools import search_hotel, search_poi, search_tips
from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.agent.tools.web_search import search_web
from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.models import Poi, SearchResult

_GROUP_CAP = {"attractions": 15, "restaurants": 8, "hotels": 6}
_WEB_KINDS = ("tips", "guide")


@dataclass(frozen=True)
class _PoiQuery:
    group: str
    keyword: str
    category: str | None = None


def attraction_queries(brief: TravelBrief) -> list[_PoiQuery]:
    city = brief.destination
    queries = [_PoiQuery("attractions", f"{city}必去景点 排名")]
    queries.extend(_PoiQuery("attractions", f"{city} {item}") for item in brief.must_visit[:3])
    queries.extend(_PoiQuery("attractions", f"{city}{item}") for item in brief.preferences[:2])
    return queries


def restaurant_queries(brief: TravelBrief) -> list[_PoiQuery]:
    city = brief.destination
    queries = [_PoiQuery("restaurants", f"{city}特色美食 推荐餐厅")]
    if brief.dietary:
        queries.append(_PoiQuery("restaurants", f"{city} {brief.dietary[0]} 餐厅"))
    return queries


def _build_jobs(
    brief: TravelBrief, ctx: ToolRegistry
) -> list[tuple[str, Awaitable[list[Poi]] | Awaitable[list[SearchResult]]]]:
    # 路由保证只有 is_ready() 的 brief 进入本节点
    if brief.start_date is None or brief.end_date is None:
        raise RuntimeError("search 节点要求 brief 日期齐备")
    poi_queries = attraction_queries(brief) + restaurant_queries(brief)
    jobs: list[tuple[str, Awaitable[list[Poi]] | Awaitable[list[SearchResult]]]] = [
        (
            f"poi:{query.group}",
            search_poi(ctx, brief.destination, query.keyword, query.category),
        )
        for query in poi_queries
    ]
    jobs.append(
        (
            "hotels",
            search_hotel(
                ctx, brief.destination, brief.start_date, brief.end_date, brief.budget_cny
            ),
        )
    )
    jobs.append(("tips", search_tips(ctx, brief.destination)))
    jobs.append(("guide", search_web(ctx, f"{brief.destination} 旅游攻略 必去景点 行程安排")))
    return jobs


def _dedupe_pois(groups: dict[str, list[Poi]]) -> CandidateGroups:
    result: CandidateGroups = {}
    for group, pois in groups.items():
        seen: set[str] = set()
        merged: list[Poi] = []
        for poi in pois:
            if poi.poi_id in seen:
                continue
            seen.add(poi.poi_id)
            merged.append(poi)
        result[group] = merged[: _GROUP_CAP[group]]
    return result


def _dedupe_web(results: list[SearchResult], cap: int = 10) -> list[SearchResult]:
    seen: set[str] = set()
    merged: list[SearchResult] = []
    for item in results:
        key = str(item.url)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[:cap]


def _sort_results(
    labels: list[str], values: list[object]
) -> tuple[CandidateGroups, list[SearchResult]]:
    groups: dict[str, list[Poi]] = {"attractions": [], "restaurants": [], "hotels": []}
    web: list[SearchResult] = []
    for kind, value in zip(labels, values, strict=True):
        if not isinstance(value, list):
            continue
        for item in value:
            if kind in _WEB_KINDS and isinstance(item, SearchResult):
                web.append(item)
            elif kind.startswith("poi:") and isinstance(item, Poi):
                groups[kind[4:]].append(item)
            elif kind == "hotels" and isinstance(item, Poi):
                groups["hotels"].append(item)
    return _dedupe_pois(groups), _dedupe_web(web)


async def gather_candidates(state: TravelState, *, ctx: ToolRegistry) -> dict[str, object]:
    brief = state["brief"]
    jobs = _build_jobs(brief, ctx)
    labels = [kind for kind, _ in jobs]
    values = await asyncio.gather(*[coro for _, coro in jobs])
    candidates, web = _sort_results(labels, list(values))
    return {
        "candidates": candidates,
        "web_results": web,
        "traces": ctx.trace_snapshot(),
    }
