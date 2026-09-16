"""PreferenceRepository 集成测试：长期偏好的 upsert 与加载。"""

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travel_agent.config import Settings
from travel_agent.db import (
    PreferenceRepository,
    create_engine,
    create_schema,
    make_session_factory,
    session_scope,
)


@pytest_asyncio.fixture
async def factory(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(settings)
    await create_schema(engine)
    yield make_session_factory(engine)
    await engine.dispose()


async def test_save_and_load_preferences(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        repo = PreferenceRepository(session)
        await repo.save(
            "thread-1",
            {"preferences": ["熊猫"], "dietary": ["辣"]},
        )

    async with session_scope(factory) as session:
        loaded = await PreferenceRepository(session).load("thread-1")
        assert loaded["preferences"] == ["熊猫"]
        assert loaded["dietary"] == ["辣"]


async def test_upsert_updates_existing_value(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        await PreferenceRepository(session).save("thread-1", {"preferences": ["熊猫"]})

    async with session_scope(factory) as session:
        await PreferenceRepository(session).save("thread-1", {"preferences": ["熊猫", "竹海"]})

    async with session_scope(factory) as session:
        loaded = await PreferenceRepository(session).load("thread-1")
        assert loaded == {"preferences": ["熊猫", "竹海"]}


async def test_empty_values_are_skipped(factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_scope(factory) as session:
        repo = PreferenceRepository(session)
        await repo.save(
            "thread-1",
            {"preferences": [], "dietary": None, "avoid": "", "pace": "relaxed"},
        )

    async with session_scope(factory) as session:
        loaded = await PreferenceRepository(session).load("thread-1")
        assert loaded == {"pace": "relaxed"}


async def test_load_empty_scope_returns_empty(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_scope(factory) as session:
        loaded = await PreferenceRepository(session).load("missing-scope")
        assert loaded == {}
