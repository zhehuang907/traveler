"""pytest 全局夹具。

数据库一律使用独立 MySQL 测试库 ``travel_agent_test``（避免污染运行库）。
- session scope：一次性建表（``Base.metadata.create_all``，幂等）。
- function autouse：每个测试开始前 TRUNCATE 全部业务表（先关外键检查）。
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Connection, inspect, text

from travel_agent.config import Settings, get_settings
from travel_agent.logging_conf import configure_logging
from travel_agent.main import create_app

# 测试库连接串（本地 MySQL 127.0.0.1:3306 root/1234）
TEST_DB_URL = os.environ.get(
    "TRAVEL_AGENT_TEST_DATABASE_URL",
    "mysql+aiomysql://root:1234@127.0.0.1:3306/travel_agent_test?charset=utf8mb4",
)

_BUSINESS_TABLES = (
    "auth_sessions",
    "conversation_contexts",
    "plan_versions",
    "messages",
    "user_preferences",
    "plans",
    "users",
)


@pytest.fixture(scope="session", autouse=True)
def _configure_logging() -> None:
    configure_logging("INFO", json_logs=False)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _disable_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试禁止读取仓库根 .env：本地真实密钥会污染「未配置」分支的断言语义。"""
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _init_test_db() -> None:
    """一次性在测试库建表（幂等）。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    from travel_agent.db import create_schema

    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    try:
        await create_schema(engine)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _truncate_tables(_init_test_db: None) -> AsyncIterator[None]:
    """每个测试前清空全部业务表。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)

    def _tables(sync_conn: Connection) -> list[str]:
        return list(inspect(sync_conn).get_table_names())

    async with engine.begin() as conn:
        await conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        tables = await conn.run_sync(_tables)
        for table in tables:
            if table.startswith("alembic_version") or table in _BUSINESS_TABLES:
                await conn.execute(text(f"TRUNCATE TABLE `{table}`"))
        await conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    await engine.dispose()
    yield


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """全部 IO 路径隔离到临时目录、DB 指向测试库的配置。"""
    return Settings(
        app_env="test",
        database_url=TEST_DB_URL,
        cache_dir=tmp_path / "cache",
        checkpoint_db=tmp_path / "checkpoints.db",
        data_dir=tmp_path,
    )


@pytest_asyncio.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://travel-agent.test") as ac:
        yield ac
