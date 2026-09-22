"""过期会话清理（需要 MySQL 测试库）：登录产生会话 → 置过期 → 清理 → me 失效。"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from tests.conftest import TEST_DB_URL

from travel_agent.config import get_settings
from travel_agent.main import create_app
from travel_agent.services.cleanup import cleanup_expired_sessions


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    import travel_agent.config as cfg_module

    cfg_module.get_settings.cache_clear()
    settings = get_settings().model_copy(
        update={
            "app_env": "test",
            "database_url": TEST_DB_URL,
            "cache_dir": tmp_path / "cache",
            "checkpoint_db": tmp_path / "cp.db",
            "data_dir": tmp_path,
            "llm_api_key": SecretStr(""),
        }
    )
    cfg_module.get_settings.cache_clear()
    original: object = cfg_module.get_settings
    cfg_module.get_settings = lambda: settings  # type: ignore[assignment]
    from travel_agent.api import deps

    deps.load_settings = lambda: settings  # type: ignore[attr-defined,assignment]
    app = create_app(settings=settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app  # type: ignore[attr-defined]  # 测试需要 app.state.session_factory
        yield c
    cfg_module.get_settings = original  # type: ignore[assignment]


async def test_cleanup_expired_sessions(client: AsyncClient) -> None:
    res = await client.post(
        "/api/auth/register", json={"username": "user1", "password": "pass1234"}
    )
    assert res.status_code == 201
    res = await client.post("/api/auth/login", json={"username": "user1", "password": "pass1234"})
    assert res.status_code == 200

    app = client.app  # type: ignore[attr-defined]
    from sqlalchemy import update

    from travel_agent.db import session_scope
    from travel_agent.db.models import AuthSessionRow

    expired = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    async with session_scope(app.state.session_factory) as session:
        await session.execute(update(AuthSessionRow).values(expires_at=expired))

    removed = await cleanup_expired_sessions(app.state.session_factory)
    assert removed == 1

    # 会话已删：当前 Cookie 失效
    res = await client.get("/api/auth/me")
    assert res.status_code == 401


async def test_cleanup_keeps_active_sessions(client: AsyncClient) -> None:
    res = await client.post(
        "/api/auth/register", json={"username": "user2", "password": "pass1234"}
    )
    assert res.status_code == 201
    res = await client.post("/api/auth/login", json={"username": "user2", "password": "pass1234"})
    assert res.status_code == 200

    app = client.app  # type: ignore[attr-defined]
    removed = await cleanup_expired_sessions(app.state.session_factory)
    assert removed == 0

    res = await client.get("/api/auth/me")
    assert res.status_code == 200
