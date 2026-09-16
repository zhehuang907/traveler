"""仓储 CRUD 与版本链测试（create_schema 建表，临时 SQLite）。"""

from collections.abc import AsyncIterator
from datetime import date

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travel_agent.config import Settings
from travel_agent.db import (
    MessageRepository,
    PlanRepository,
    create_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan


def _plan(plan_id: str = "plan-1", cost: float = 100.0) -> TripPlan:
    return TripPlan(
        plan_id=plan_id,
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
                    PlanItem(
                        item_id="d1-1",
                        title="武侯祠",
                        category="attraction",
                        cost_cny=cost,
                    )
                ],
            )
        ],
    )


@pytest_asyncio.fixture
async def factory(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(settings)
    await create_schema(engine)
    yield make_session_factory(engine)
    await engine.dispose()


async def test_save_snapshot_creates_plan_and_version(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    plan = _plan()
    async with session_scope(factory) as session:
        await PlanRepository(session).save_snapshot(plan, "thread-1", version=1)

    async with session_scope(factory) as session:
        repo = PlanRepository(session)
        loaded = await repo.get_plan("plan-1")
        assert loaded is not None
        assert loaded.days[0].items[0].title == "武侯祠"
        assert await repo.list_version_numbers("plan-1") == [1]
        version1 = await repo.get_version("plan-1", 1)
        assert version1 is not None and version1.destination == "成都"
        assert await repo.get_version("plan-1", 9) is None
        assert await repo.get_plan("missing") is None


async def test_upsert_appends_versions(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        repo = PlanRepository(session)
        await repo.save_snapshot(_plan(cost=100), "t", version=1)
        await repo.save_snapshot(_plan(cost=200), "t", version=2, diff_json='{"reason": "x"}')

    async with session_scope(factory) as session:
        repo = PlanRepository(session)
        assert await repo.list_version_numbers("plan-1") == [1, 2]
        latest = await repo.get_plan("plan-1")
        assert latest is not None and latest.total_cost_cny == 200


async def test_message_repository_listing(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        messages = MessageRepository(session)
        await messages.add("t1", "user", "去成都")
        await messages.add("t1", "assistant", "好的")
        await messages.add("t2", "user", "别的会话")

    async with session_scope(factory) as session:
        rows = await MessageRepository(session).list("t1")
        assert [(row.role, row.content) for row in rows] == [
            ("user", "去成都"),
            ("assistant", "好的"),
        ]
