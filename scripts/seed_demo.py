"""演示数据填充：成都 4 天行程 + 消息 + 版本快照 + 偏好。

用法：uv run python scripts/seed_demo.py
幂等：重复执行不会产生重复数据（先清空 thread_id=demo 的旧数据）。

填完后可通过以下方式体验：
- 对话页：http://localhost:8000/
- 行程页：http://localhost:8000/plan/{plan_id}（输出中会打印）
- API：GET /api/plan/{plan_id} / GET /api/plan/{plan_id}/versions
"""

import asyncio
from datetime import date, timedelta

from travel_agent.config import get_settings
from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.models import GeoPoint, Poi
from travel_agent.domain.plan import TripPlan
from travel_agent.domain.plan_builder import CandidateCatalog


def _build_plan() -> TripPlan:
    """从 fixture 数据构建一份合规的成都 4 天行程。"""
    brief = TravelBrief(
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 4),
        travelers=2,
        budget_cny=5000,
        pace="moderate",
        preferences=["熊猫"],
        dietary=["辣"],
        must_visit=["武侯祠"],
    )

    def poi(ref: str, name: str, cost: int, lat: float, lng: float) -> Poi:
        return Poi(
            poi_id=ref,
            name=name,
            location=GeoPoint(lat=lat, lng=lng),
            cost_hint=f"{cost}元",
            provider="amap",
        )

    attractions = [
        poi("a1", "武侯祠", 50, 30.61, 104.01),
        poi("a2", "锦里古街", 0, 30.62, 104.02),
        poi("a3", "宽窄巷子", 0, 30.63, 104.03),
        poi("a4", "成都大熊猫繁育研究基地", 55, 30.64, 104.04),
        poi("a5", "杜甫草堂", 50, 30.65, 104.05),
    ]
    restaurants = [
        poi("r1", "蜀大侠火锅", 100, 30.66, 104.06),
        poi("r2", "陈麻婆豆腐", 60, 30.67, 104.07),
    ]
    hotels = [poi("h1", "春熙路商务酒店", 300, 30.68, 104.08)]

    candidates = CandidateCatalog.from_groups(
        attractions=attractions, restaurants=restaurants, hotels=hotels
    )

    # 按候选编号水合（编号 1-5 景点，6-7 餐饮，8 酒店）
    from travel_agent.domain.draft import DraftDay, DraftItem, PlanDraft

    specs: list[list[tuple[int, str, str, str, int]]] = [
        [(1, "09:00", "11:00", "attraction", 50), (2, "11:30", "13:00", "attraction", 0)],
        [(4, "08:30", "12:00", "attraction", 55), (6, "12:30", "13:30", "restaurant", 100)],
        [(3, "09:30", "11:30", "attraction", 0), (7, "12:00", "13:00", "restaurant", 60)],
        [(5, "09:00", "11:00", "attraction", 50), (8, "00:00", "23:59", "hotel", 300)],
    ]
    days = []
    for i, items in enumerate(specs):
        days.append(
            DraftDay(
                date=date(2026, 10, 1) + timedelta(days=i),
                items=[
                    DraftItem(
                        title=f"候选{ref}",
                        category=cat,
                        candidate_ref=ref,
                        start_clock=start,
                        end_clock=end,
                        cost_cny=cost,
                    )
                    for ref, start, end, cat, cost in items
                ],
            )
        )

    draft = PlanDraft(
        summary="成都4天3晚行程：武侯祠锦里文化之旅+熊猫基地+宽窄巷子+杜甫草堂",
        days=days,
        tips=["火锅建议提前排号", "熊猫基地上午人少", "宽窄巷子傍晚最有氛围"],
    )

    from travel_agent.domain.plan_builder import hydrate_plan

    result = hydrate_plan(draft, brief, candidates, {}, None)
    return result.plan


async def main() -> None:
    from travel_agent.db import (
        MessageRepository,
        PlanRepository,
        PreferenceRepository,
        create_engine,
        create_schema,
        make_session_factory,
        session_scope,
    )

    settings = get_settings()
    settings.ensure_dirs()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = make_session_factory(engine)

    thread_id = "demo"
    plan = _build_plan()

    async with session_scope(factory) as session:
        # 保存行程快照
        repo = PlanRepository(session)
        await repo.save_snapshot(plan, thread_id, 1, trigger_message_id="demo_seed")
        # 消息
        msgs = MessageRepository(session)
        await msgs.add(thread_id, "user", "成都4天，预算5000，爱吃辣")
        await msgs.add(
            thread_id,
            "assistant",
            "为你规划了成都4天行程：武侯祠+锦里、熊猫基地、宽窄巷子+杜甫草堂，预算内。",
        )
        # 偏好
        prefs = PreferenceRepository(session)
        await prefs.save(
            thread_id,
            {
                "preferences": ["熊猫"],
                "dietary": ["辣"],
                "must_visit": ["武侯祠"],
                "pace": "moderate",
            },
        )

    await engine.dispose()
    print(f"演示数据填充完成。plan_id={plan.plan_id} thread_id={thread_id}")
    print(f"行程页：http://localhost:8000/plan/{plan.plan_id}")
    print("对话页：http://localhost:8000/")


if __name__ == "__main__":
    asyncio.run(main())
