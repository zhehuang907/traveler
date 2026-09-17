"""API 端点测试：页面渲染、认证、plan CRUD、share、SSE 错误路径。

用 httpx.AsyncClient + FastAPI TestClient，DB 走 MySQL 测试库（conftest 已建表清库）。
受保护端点需要先注册+登录（HttpOnly Cookie 由 httpx 自动维持）。
"""

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from tests.conftest import TEST_DB_URL

from travel_agent.config import get_settings
from travel_agent.domain.plan import PlanDay, PlanItem, TripPlan, new_plan_id
from travel_agent.main import create_app


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
            "llm_api_key": "",
        }
    )
    cfg_module.get_settings.cache_clear()
    original: Any = cfg_module.get_settings
    cfg_module.get_settings = lambda: settings  # type: ignore[assignment]
    from travel_agent.api import deps

    deps.load_settings = lambda: settings  # type: ignore[attr-defined,assignment]
    app = create_app(settings=settings)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    cfg_module.get_settings = original


def _trip_plan() -> TripPlan:
    return TripPlan(
        plan_id=new_plan_id(),
        destination="成都",
        start_date=date(2026, 10, 1),
        end_date=date(2026, 10, 4),
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
                        cost_cny=50,
                    )
                ],
            ),
        ],
    )


async def _register_and_login(client: AsyncClient, username: str = "alice") -> None:
    res = await client.post(
        "/api/auth/register", json={"username": username, "password": "pass1234"}
    )
    assert res.status_code == 201
    res = await client.post("/api/auth/login", json={"username": username, "password": "pass1234"})
    assert res.status_code == 200


async def test_index_page(client: AsyncClient) -> None:
    res = await client.get("/")
    assert res.status_code == 200
    assert "小T" in res.text
    assert "你好，我是小T" in res.text


async def test_plan_page(client: AsyncClient) -> None:
    res = await client.get("/plan/abc123")
    assert res.status_code == 200
    assert "abc123" in res.text


async def test_share_page(client: AsyncClient) -> None:
    res = await client.get("/share/sometoken")
    assert res.status_code == 200
    assert "sometoken" in res.text


# ---------- 认证 ----------


async def test_register_login_and_me(client: AsyncClient) -> None:
    await _register_and_login(client)
    res = await client.get("/api/auth/me")
    assert res.status_code == 200
    assert res.json()["username"] == "alice"


async def test_register_duplicate(client: AsyncClient) -> None:
    await _register_and_login(client)
    res = await client.post(
        "/api/auth/register", json={"username": "alice", "password": "another1"}
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "BAD_REQUEST"


async def test_login_wrong_password(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json={"username": "bob", "password": "pass1234"})
    res = await client.post("/api/auth/login", json={"username": "bob", "password": "wrong123"})
    assert res.status_code == 401


async def test_me_unauthorized(client: AsyncClient) -> None:
    res = await client.get("/api/auth/me")
    assert res.status_code == 401


# ---------- 受保护端点：需登录 ----------


async def test_chat_requires_auth(client: AsyncClient) -> None:
    res = await client.post("/api/chat", json={"message": "成都4天"})
    assert res.status_code == 401


async def test_chat_no_api_key(client: AsyncClient) -> None:
    await _register_and_login(client)
    res = await client.post("/api/chat", json={"message": "成都4天"})
    assert res.status_code == 503
    data = res.json()
    assert data["error"]["code"] == "LLM_NOT_CONFIGURED"


async def test_chat_empty_message(client: AsyncClient) -> None:
    await _register_and_login(client)
    res = await client.post("/api/chat", json={"message": ""})
    assert res.status_code == 422


async def test_plan_list_empty(client: AsyncClient) -> None:
    await _register_and_login(client)
    res = await client.get("/api/plan")
    assert res.status_code == 200
    assert res.json() == []


async def test_plan_not_found(client: AsyncClient) -> None:
    await _register_and_login(client)
    res = await client.get("/api/plan/nonexistent")
    assert res.status_code == 404


async def test_plan_requires_auth(client: AsyncClient) -> None:
    res = await client.get("/api/plan")
    assert res.status_code == 401


# ---------- share ----------


async def test_share_create_and_get(client: AsyncClient) -> None:
    """分享创建 → 读取闭环（登录用户先建行程再分享）。"""
    await _register_and_login(client)
    plan = _trip_plan()
    res = await client.post(
        "/api/plan",
        json={
            "destination": plan.destination,
            "start_date": plan.start_date.isoformat(),
            "end_date": plan.end_date.isoformat(),
            "travelers": plan.travelers,
            "budget_cny": plan.budget_cny,
        },
    )
    assert res.status_code == 200
    plan_id = res.json()["plan"]["plan_id"]

    res = await client.post("/api/share", json={"plan_id": plan_id})
    assert res.status_code == 200
    token = res.json()["token"]
    assert len(token) > 20

    res2 = await client.get(f"/api/share/{token}")
    assert res2.status_code == 200
    assert res2.json()["plan"]["destination"] == "成都"


async def test_share_requires_auth_create(client: AsyncClient) -> None:
    res = await client.post("/api/share", json={"plan_id": "x"})
    assert res.status_code == 401


async def test_pdf_endpoint_returns_503(client: AsyncClient) -> None:
    await _register_and_login(client)
    res = await client.post("/api/plan/abc/pdf")
    assert res.status_code == 503


# ---------- CRUD ----------


async def test_plan_crud_flow(client: AsyncClient) -> None:
    await _register_and_login(client)
    # 创建
    res = await client.post(
        "/api/plan",
        json={
            "destination": "北京",
            "start_date": "2026-11-01",
            "end_date": "2026-11-03",
            "travelers": 3,
            "budget_cny": 3000,
        },
    )
    assert res.status_code == 200
    plan_id = res.json()["plan"]["plan_id"]
    assert res.json()["plan"]["destination"] == "北京"

    # 列表含该行程
    res = await client.get("/api/plan")
    assert res.status_code == 200
    ids = [item["id"] for item in res.json()]
    assert plan_id in ids

    # 详情（本人可见）
    res = await client.get(f"/api/plan/{plan_id}")
    assert res.status_code == 200
    assert res.json()["plan"]["destination"] == "北京"

    # 删除
    res = await client.delete(f"/api/plan/{plan_id}")
    assert res.status_code == 204
    res = await client.get(f"/api/plan/{plan_id}")
    assert res.status_code == 404


async def test_plan_access_control(client: AsyncClient) -> None:
    """用户 A 无法读到用户 B 的行程。"""
    await _register_and_login(client, "alice")
    res = await client.post(
        "/api/plan",
        json={
            "destination": "上海",
            "start_date": "2026-12-01",
            "end_date": "2026-12-02",
            "travelers": 2,
        },
    )
    plan_id = res.json()["plan"]["plan_id"]

    # 登出后换 bob 登录
    await client.post("/api/auth/logout")
    await _register_and_login(client, "bob")
    res = await client.get(f"/api/plan/{plan_id}")
    assert res.status_code == 404
    res = await client.delete(f"/api/plan/{plan_id}")
    assert res.status_code == 404
