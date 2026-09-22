"""API 端点测试：页面渲染、认证、plan CRUD、share、SSE 错误路径、手动编辑与 AI 优化。

用 httpx.AsyncClient + FastAPI TestClient，DB 走 MySQL 测试库（conftest 已建表清库）。
受保护端点需要先注册+登录（HttpOnly Cookie 由 httpx 自动维持）。
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from agent_fakes import FakeLLM
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from tests.conftest import TEST_DB_URL

from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.api.deps import get_llm, get_tool_context
from travel_agent.api.routes_chat import DocDigest
from travel_agent.config import get_settings
from travel_agent.domain.draft import DraftDay, DraftItem, PlanDraft
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
            # 注意：model_copy 跳过校验，SecretStr 字段必须传 SecretStr 保持类型一致
            "llm_api_key": SecretStr(""),
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
        c.app = app  # type: ignore[attr-defined]  # 测试通过它覆盖 FastAPI 依赖（LLM/工具替身）
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


async def test_change_password_flow(client: AsyncClient) -> None:
    """改密闭环：旧密校验 → 更新 → 旧会话吊销需重登 → 新密码可登录。"""
    await _register_and_login(client)

    # 原密码错误
    res = await client.post(
        "/api/auth/change-password",
        json={"old_password": "wrong123", "new_password": "newpass456"},
    )
    assert res.status_code == 400
    assert "原密码错误" in res.json()["error"]["message"]

    # 新密码不合规（纯数字）
    res = await client.post(
        "/api/auth/change-password",
        json={"old_password": "pass1234", "new_password": "12345678"},
    )
    assert res.status_code == 422

    # 新旧密码相同
    res = await client.post(
        "/api/auth/change-password",
        json={"old_password": "pass1234", "new_password": "pass1234"},
    )
    assert res.status_code == 400

    # 正确改密
    res = await client.post(
        "/api/auth/change-password",
        json={"old_password": "pass1234", "new_password": "newpass456"},
    )
    assert res.status_code == 204

    # 旧会话被吊销：me 401
    res = await client.get("/api/auth/me")
    assert res.status_code == 401

    # 新密码可登录
    res = await client.post("/api/auth/login", json={"username": "alice", "password": "newpass456"})
    assert res.status_code == 200


async def test_change_password_requires_auth(client: AsyncClient) -> None:
    res = await client.post(
        "/api/auth/change-password",
        json={"old_password": "x", "new_password": "y1234567"},
    )
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


async def test_share_mine_and_revoke(client: AsyncClient) -> None:
    """我的分享列表与撤销（仅创建者可撤销）。"""
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

    # 我的分享列表包含刚创建的分享
    res = await client.get("/api/share/mine")
    assert res.status_code == 200
    items = res.json()
    assert any(item["token"] == token and item["destination"] == "成都" for item in items)

    # 撤销后：列表不再包含，且只读链接 404
    res = await client.delete(f"/api/share/{token}")
    assert res.status_code == 204
    res = await client.get("/api/share/mine")
    assert all(item["token"] != token for item in res.json())
    res = await client.get(f"/api/share/{token}")
    assert res.status_code == 404


async def test_share_mine_requires_auth(client: AsyncClient) -> None:
    res = await client.get("/api/share/mine")
    assert res.status_code == 401


async def test_share_revoke_requires_auth(client: AsyncClient) -> None:
    res = await client.delete("/api/share/abc")
    assert res.status_code == 401


async def test_share_revoke_not_owner(client: AsyncClient) -> None:
    """非创建者撤销他人分享 → 404。"""
    await _register_and_login(client, username="alice")
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
    plan_id = res.json()["plan"]["plan_id"]
    res = await client.post("/api/share", json={"plan_id": plan_id})
    token = res.json()["token"]

    await _register_and_login(client, username="bob")
    res = await client.delete(f"/api/share/{token}")
    assert res.status_code == 404


async def test_pdf_export_requires_login(client: AsyncClient) -> None:
    """PDF 导出受保护：未登录 401；登录后按环境给出 200（本机有 Playwright）或 503（CI 无）。"""
    res = await client.post("/api/plan/abc/pdf")
    assert res.status_code == 401

    await _register_and_login(client)
    res = await client.post("/api/plan/abc/pdf")
    # 未找到行程 → 404（先于 PDF 能力检查）
    assert res.status_code == 404


async def test_pdf_export_returns_pdf_or_degraded(client: AsyncClient) -> None:
    """已有行程的 PDF 导出：本机 Playwright 可用 → 200 + application/pdf；不可用 → 503 降级。"""
    await _register_and_login(client)
    plan_id = await _create_plan(client)
    try:
        import playwright  # noqa: F401
    except ImportError:
        res = await client.post(f"/api/plan/{plan_id}/pdf")
        assert res.status_code == 503
        return
    res = await client.post(f"/api/plan/{plan_id}/pdf")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/pdf")
    assert res.content[:4] == b"%PDF"


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


async def test_plan_clone_flow(client: AsyncClient) -> None:
    """克隆生成全新独立行程：新 id、版本从 1 开始、内容一致；非本人 404。"""
    await _register_and_login(client)
    res = await client.post(
        "/api/plan",
        json={
            "destination": "杭州",
            "start_date": "2026-12-01",
            "end_date": "2026-12-02",
            "travelers": 2,
            "budget_cny": 1500,
        },
    )
    assert res.status_code == 200
    plan_id = res.json()["plan"]["plan_id"]

    res = await client.post(f"/api/plan/{plan_id}/clone")
    assert res.status_code == 200
    data = res.json()
    clone_id = data["plan"]["plan_id"]
    assert clone_id != plan_id
    assert data["plan"]["destination"] == "杭州"
    assert data["plan_version"] == 1

    # 两个行程都在列表，且均可独立访问
    res = await client.get("/api/plan")
    ids = [item["id"] for item in res.json()]
    assert plan_id in ids and clone_id in ids
    res = await client.get(f"/api/plan/{clone_id}")
    assert res.status_code == 200
    assert res.json()["plan"]["destination"] == "杭州"

    # 删除原行程不影响克隆
    res = await client.delete(f"/api/plan/{plan_id}")
    assert res.status_code == 204
    res = await client.get(f"/api/plan/{clone_id}")
    assert res.status_code == 200

    # 克隆不存在的行程 → 404
    res = await client.post("/api/plan/nonexistent/clone")
    assert res.status_code == 404


async def test_plan_clone_requires_auth(client: AsyncClient) -> None:
    res = await client.post("/api/plan/abc/clone")
    assert res.status_code == 401


async def test_plan_search_and_pagination(client: AsyncClient) -> None:
    """关键词搜索（标题/目的地）+ 分页 + X-Total-Count 总数头。"""
    await _register_and_login(client)
    for dest, days in [("北京", 2), ("上海", 3), ("广州", 2)]:
        res = await client.post(
            "/api/plan",
            json={
                "destination": dest,
                "start_date": "2026-12-01",
                "end_date": f"2026-12-{1 + days:02d}",
                "travelers": 2,
            },
        )
        assert res.status_code == 200

    # 搜索命中"北京"（title 含目的地）
    res = await client.get("/api/plan", params={"q": "北京"})
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["destination"] == "北京"
    assert res.headers.get("X-Total-Count") == "1"

    # 分页：limit=2 返回 2 条，总数 3
    res = await client.get("/api/plan", params={"limit": 2})
    assert res.status_code == 200
    assert len(res.json()) == 2
    assert res.headers.get("X-Total-Count") == "3"

    # 第二页取剩余
    res = await client.get("/api/plan", params={"limit": 2, "offset": 2})
    assert res.status_code == 200
    assert len(res.json()) == 1


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


# ---------- 阶段八：手动编辑与 AI 优化 ----------


async def _create_plan(
    client: AsyncClient,
    *,
    destination: str = "成都",
    start: str = "2026-10-01",
    end: str = "2026-10-03",
    travelers: int = 2,
    budget_cny: float = 5000,
) -> str:
    res = await client.post(
        "/api/plan",
        json={
            "destination": destination,
            "start_date": start,
            "end_date": end,
            "travelers": travelers,
            "budget_cny": budget_cny,
        },
    )
    assert res.status_code == 200
    return str(res.json()["plan"]["plan_id"])


def _editable_payload(plan: dict[str, Any], *, summary: str = "手动编辑后的行程") -> dict[str, Any]:
    """基于 GET 的 plan 构造「每天一条活动」的编辑后版本（保留计算字段，模拟前端原样回传）。"""
    cursor = date.fromisoformat(plan["start_date"])
    end = date.fromisoformat(plan["end_date"])
    days: list[dict[str, Any]] = []
    index = 1
    while cursor <= end:
        days.append(
            {
                "day_index": index,
                "date": cursor.isoformat(),
                "items": [
                    {
                        "item_id": f"tmp-{index}",
                        "title": f"第{index}天自由活动",
                        "category": "activity",
                        "duration_min": 120,
                        "cost_cny": 100,
                    }
                ],
                "note": "",
            }
        )
        cursor += timedelta(days=1)
        index += 1
    updated = dict(plan)
    updated["days"] = days
    updated["summary"] = summary
    return updated


async def test_plan_manual_edit_creates_version(client: AsyncClient) -> None:
    await _register_and_login(client)
    plan_id = await _create_plan(client)
    plan = (await client.get(f"/api/plan/{plan_id}")).json()["plan"]
    res = await client.put(f"/api/plan/{plan_id}", json={"plan": _editable_payload(plan)})
    assert res.status_code == 200
    data = res.json()
    assert data["plan_version"] == 2
    assert len(data["diff"]["added"]) == 3
    assert data["plan"]["summary"] == "手动编辑后的行程"
    assert data["plan"]["total_cost_cny"] == 300.0

    res = await client.get(f"/api/plan/{plan_id}/versions")
    versions = res.json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["trigger_snippet"] is None
    assert versions[1]["trigger_snippet"] == "手动编辑"
    assert versions[1]["created_at"]

    # 幂等：内容无变化重复保存不产生新版本
    res = await client.put(f"/api/plan/{plan_id}", json={"plan": data["plan"]})
    assert res.status_code == 200
    assert res.json()["plan_version"] == 2


async def test_plan_manual_edit_rejects_skeleton_change(client: AsyncClient) -> None:
    await _register_and_login(client)
    plan_id = await _create_plan(client)
    plan = (await client.get(f"/api/plan/{plan_id}")).json()["plan"]
    payload = _editable_payload(plan)
    payload["travelers"] = 9
    res = await client.put(f"/api/plan/{plan_id}", json={"plan": payload})
    assert res.status_code == 422
    assert "出行人数" in res.json()["error"]["message"]


async def test_plan_manual_edit_rejects_unknown_poi(client: AsyncClient) -> None:
    await _register_and_login(client)
    plan_id = await _create_plan(client)
    plan = (await client.get(f"/api/plan/{plan_id}")).json()["plan"]
    payload = _editable_payload(plan)
    payload["days"][0]["items"].append(
        {"item_id": "tmp-x", "title": "手填酒店", "category": "hotel", "duration_min": 60}
    )
    res = await client.put(f"/api/plan/{plan_id}", json={"plan": payload})
    assert res.status_code == 422


async def test_plan_manual_edit_access_control(client: AsyncClient) -> None:
    res = await client.put(
        "/api/plan/nonexistent", json={"plan": _trip_plan().model_dump(mode="json")}
    )
    assert res.status_code == 401

    await _register_and_login(client, "alice")
    plan_id = await _create_plan(client)
    plan = (await client.get(f"/api/plan/{plan_id}")).json()["plan"]
    payload = _editable_payload(plan)

    await client.post("/api/auth/logout")
    await _register_and_login(client, "bob")
    res = await client.put(f"/api/plan/{plan_id}", json={"plan": payload})
    assert res.status_code == 404


async def test_plan_optimize_requires_llm_config(client: AsyncClient) -> None:
    await _register_and_login(client)
    plan_id = await _create_plan(client)
    res = await client.post(f"/api/plan/{plan_id}/optimize", json={})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "LLM_NOT_CONFIGURED"


def _optimize_draft() -> PlanDraft:
    """3 天草稿：第 2 天入住候选目录中的酒店（编号 1 来自补充检索）。"""
    return PlanDraft(
        days=[
            DraftDay(
                date=date(2026, 10, 1),
                items=[
                    DraftItem(
                        title="自由活动",
                        category="activity",
                        start_clock="09:00",
                        end_clock="11:00",
                        duration_min=120,
                    )
                ],
            ),
            DraftDay(
                date=date(2026, 10, 2),
                items=[
                    DraftItem(
                        title="入住酒店",
                        category="hotel",
                        candidate_ref=1,
                        start_clock="14:00",
                        end_clock="15:00",
                        duration_min=60,
                    )
                ],
            ),
            DraftDay(
                date=date(2026, 10, 3),
                items=[
                    DraftItem(
                        title="自由活动",
                        category="activity",
                        start_clock="09:00",
                        end_clock="11:00",
                        duration_min=120,
                    )
                ],
            ),
        ],
        summary="优化后的行程",
        tips=["提前订票"],
    )


async def test_plan_optimize_flow_and_rollback(client: AsyncClient, registry: ToolRegistry) -> None:
    """编辑保存（v2）→ AI 优化（v3，命中真实候选）→ 回滚到 v2（v4）的完整闭环。"""
    await _register_and_login(client)
    plan_id = await _create_plan(client)
    plan = (await client.get(f"/api/plan/{plan_id}")).json()["plan"]
    res = await client.put(f"/api/plan/{plan_id}", json={"plan": _editable_payload(plan)})
    assert res.status_code == 200

    app = client.app  # type: ignore[attr-defined]
    app.dependency_overrides[get_llm] = lambda: FakeLLM(drafts=[_optimize_draft()])
    app.dependency_overrides[get_tool_context] = lambda: registry
    try:
        res = await client.post(
            f"/api/plan/{plan_id}/optimize", json={"instruction": "换一家酒店住得更舒服"}
        )
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200
    data = res.json()
    assert data["plan_version"] == 3
    assert data["plan"]["plan_id"] == plan_id
    assert data["warnings"] == []
    day2_titles = [item["title"] for item in data["plan"]["days"][1]["items"]]
    assert "春熙路商务酒店" in day2_titles  # FakeMaps 酒店候选经水合进入行程

    res = await client.get(f"/api/plan/{plan_id}/versions")
    versions = res.json()
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[2]["trigger_snippet"] == "AI 优化"

    # 回滚到编辑后版本（v2）：酒店消失；内容与 v2 一致，命中内容去重不再堆叠版本行
    res = await client.post(f"/api/plan/{plan_id}/rollback", json={"version": 2})
    assert res.status_code == 200
    assert res.json()["plan_version"] == 2
    titles = [item["title"] for day in res.json()["plan"]["days"] for item in day["items"]]
    assert "春熙路商务酒店" not in titles
    assert "第2天自由活动" in titles


# ---------- 聊天窗口文档导入 ----------


class _DigestLLM:
    """upload 端点替身：aparse 固定返回预置提炼要点。"""

    def __init__(self, text: str = "目的地：昆明\n预算：3000元") -> None:
        self._text = text
        self.user_prompt: str | None = None

    async def aparse(self, schema: type[Any], *, system: str, user: str) -> Any:
        self.user_prompt = user
        assert schema is DocDigest
        return schema(text=self._text)


async def test_chat_upload_requires_auth(client: AsyncClient) -> None:
    res = await client.post("/api/chat/upload", files={"file": ("trip.txt", b"Day1", "text/plain")})
    assert res.status_code == 401


async def test_chat_upload_returns_digest_text(client: AsyncClient) -> None:
    await _register_and_login(client)
    app = client.app  # type: ignore[attr-defined]
    digest = _DigestLLM(text="目的地：昆明\n预算：3000元\n必去：滇池")
    app.dependency_overrides[get_llm] = lambda: digest
    try:
        res = await client.post(
            "/api/chat/upload",
            files={"file": ("攻略.txt", "Day1: 滇池\n预算 3000".encode(), "text/plain")},
        )
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 200
    data = res.json()
    assert data["filename"] == "攻略.txt"
    assert data["text"] == "目的地：昆明\n预算：3000元\n必去：滇池"
    # LLM 收到的 user 消息包含文档原文（供提炼），不含返回全文之外的拼接
    assert digest.user_prompt is not None
    assert "滇池" in digest.user_prompt


async def test_chat_upload_requires_llm(client: AsyncClient) -> None:
    # 未配置 LLM_API_KEY → get_llm 返回 None → 503
    await _register_and_login(client)
    res = await client.post("/api/chat/upload", files={"file": ("trip.txt", b"Day1", "text/plain")})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "LLM_NOT_CONFIGURED"


async def test_chat_upload_rejects_unsupported_format(client: AsyncClient) -> None:
    await _register_and_login(client)
    res = await client.post(
        "/api/chat/upload", files={"file": ("trip.exe", b"payload", "application/octet-stream")}
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "BAD_REQUEST"


async def test_chat_upload_rejects_unreadable_content(client: AsyncClient) -> None:
    await _register_and_login(client)
    res = await client.post(
        "/api/chat/upload", files={"file": ("scan.pdf", b"not-a-real-pdf", "application/pdf")}
    )
    assert res.status_code == 400
    assert "PDF" in res.json()["error"]["message"]


# ---------- 对话历史（P0-1） ----------


class _FakeRequest:
    """仅暴露 _persist 所需的最小 request 结构（app.state.session_factory）。"""

    def __init__(self, app: Any) -> None:
        self.app = app


async def test_chat_history_persisted_and_isolated(client: AsyncClient) -> None:
    """对话历史闭环：_persist 落库 user+assistant 消息 → history 返回；他人不可见。"""
    await _register_and_login(client)
    from travel_agent.api.routes_chat import _persist

    app = client.app  # type: ignore[attr-defined]
    thread_id = "historythread001"
    await _persist(
        _FakeRequest(app),  # type: ignore[arg-type]  # 测试替身仅需 app.state.session_factory
        user_id=1,
        thread_id=thread_id,
        final={"reply": "这是给你的回答。"},
        message="你好",
    )

    res = await client.get(f"/api/chat/{thread_id}/history")
    assert res.status_code == 200
    rows = res.json()
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "你好"
    assert rows[1]["content"] == "这是给你的回答。"

    # 用户隔离：bob 看不到 alice 的历史
    await client.post("/api/auth/logout")
    await _register_and_login(client, "bob")
    res = await client.get(f"/api/chat/{thread_id}/history")
    assert res.status_code == 200
    assert res.json() == []


async def test_chat_history_requires_auth(client: AsyncClient) -> None:
    res = await client.get("/api/chat/somethread/history")
    assert res.status_code == 401


async def test_chat_load_history_messages_backfills_context(client: AsyncClient) -> None:
    """历史回灌：解答问题前把该会话 user/assistant 消息按序转成图上下文，且受预算截断。"""
    await _register_and_login(client)
    from travel_agent.api.routes_chat import _load_history_messages, _persist

    app = client.app  # type: ignore[attr-defined]
    thread_id = "historybackfill001"
    for text, reply in (("第一轮提问", "第一轮回答"), ("第二轮提问", "第二轮回答")):
        await _persist(
            _FakeRequest(app),  # type: ignore[arg-type]
            user_id=1,
            thread_id=thread_id,
            final={"reply": reply},
            message=text,
        )

    msgs = await _load_history_messages(_FakeRequest(app), user_id=1, thread_id=thread_id)  # type: ignore[arg-type]
    expect = [
        ("HumanMessage", "第一轮提问"),
        ("AIMessage", "第一轮回答"),
        ("HumanMessage", "第二轮提问"),
        ("AIMessage", "第二轮回答"),
    ]
    assert [(type(m).__name__, m.content) for m in msgs] == expect

    # 字符预算不足时只保留末尾连续的最近一条
    msgs = await _load_history_messages(
        _FakeRequest(app),  # type: ignore[arg-type]
        user_id=1,
        thread_id=thread_id,
        max_chars=3,
    )
    assert [(type(m).__name__, m.content) for m in msgs] == [("AIMessage", "第二轮回答")]


async def test_chat_persist_plan_failure_keeps_messages(client: AsyncClient) -> None:
    """对话消息与行程档案分属独立事务：plan 落库失败时消息不能一起回滚。"""
    await _register_and_login(client)
    from travel_agent.api.routes_chat import _persist

    app = client.app  # type: ignore[attr-defined]
    thread_id = "persistsplit001"

    # final 携带非法 plan（缺必填字段），模拟 save_snapshot 抛错
    await _persist(
        _FakeRequest(app),  # type: ignore[arg-type]
        user_id=1,
        thread_id=thread_id,
        final={"reply": "回答仍在。", "plan": {"bad": "plan", "days": []}},
        message="请规划",
    )

    # 消息不受 plan 失败影响，history 照常返回
    res = await client.get(f"/api/chat/{thread_id}/history")
    assert res.status_code == 200
    rows = res.json()
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "回答仍在。"


async def test_chat_threads_list_and_delete(client: AsyncClient) -> None:
    """会话聚合列表 + 删除会话（按用户隔离）。"""
    await _register_and_login(client)
    from travel_agent.api.routes_chat import _persist

    app = client.app  # type: ignore[attr-defined]
    thread_a = "threadlist001"
    thread_b = "threadlist002"
    for tid, reply in ((thread_a, "回答A"), (thread_b, "回答B")):
        await _persist(
            _FakeRequest(app),  # type: ignore[arg-type]
            user_id=1,
            thread_id=tid,
            final={"reply": reply},
            message="你好",
        )

    res = await client.get("/api/chat/threads")
    assert res.status_code == 200
    items = res.json()
    tids = [item["thread_id"] for item in items]
    assert thread_a in tids and thread_b in tids
    by_id = {item["thread_id"]: item for item in items}
    assert by_id[thread_a]["preview"] == "回答A"

    # 删除会话 A：列表不再包含，history 清空
    res = await client.delete(f"/api/chat/threads/{thread_a}")
    assert res.status_code == 204
    res = await client.get("/api/chat/threads")
    assert all(item["thread_id"] != thread_a for item in res.json())
    res = await client.get(f"/api/chat/{thread_a}/history")
    assert res.json() == []

    # 用户隔离：bob 看不到 alice 的会话，删除 alice 的会话 404
    await client.post("/api/auth/logout")
    await _register_and_login(client, username="bob")
    res = await client.get("/api/chat/threads")
    assert res.json() == []
    res = await client.delete(f"/api/chat/threads/{thread_b}")
    assert res.status_code == 404


async def test_chat_threads_requires_auth(client: AsyncClient) -> None:
    res = await client.get("/api/chat/threads")
    assert res.status_code == 401


# ---------- 行程版本号（P0-2） ----------


async def test_get_plan_returns_latest_version(client: AsyncClient) -> None:
    """GET 详情返回真实最新版本号（编辑一次后应为 v2，而非恒 0）。"""
    await _register_and_login(client)
    plan_id = await _create_plan(client)
    plan = (await client.get(f"/api/plan/{plan_id}")).json()["plan"]
    res = await client.put(f"/api/plan/{plan_id}", json={"plan": _editable_payload(plan)})
    assert res.status_code == 200
    res = await client.get(f"/api/plan/{plan_id}")
    assert res.status_code == 200
    assert res.json()["plan_version"] == 2


# ---------- 登录防爆破（P0-3） ----------


async def test_login_throttle_locks_after_failures(client: AsyncClient) -> None:
    """连续失败达到阈值后锁定：即使密码正确也 429。"""
    await client.post("/api/auth/register", json={"username": "carol", "password": "pass1234"})
    for _ in range(5):
        res = await client.post(
            "/api/auth/login", json={"username": "carol", "password": "badpass1"}
        )
        assert res.status_code == 401
    res = await client.post("/api/auth/login", json={"username": "carol", "password": "pass1234"})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "RATE_LIMITED"


async def test_login_throttle_success_clears(client: AsyncClient) -> None:
    """成功登录清除失败计数：后续尝试不再受限。"""
    await client.post("/api/auth/register", json={"username": "frank", "password": "pass1234"})
    for _ in range(3):
        res = await client.post(
            "/api/auth/login", json={"username": "frank", "password": "badpass1"}
        )
        assert res.status_code == 401
    res = await client.post("/api/auth/login", json={"username": "frank", "password": "pass1234"})
    assert res.status_code == 200
    # 清除后再失败 4 次仍不锁定（未达 5 次阈值）
    for _ in range(4):
        res = await client.post(
            "/api/auth/login", json={"username": "frank", "password": "badpass1"}
        )
        assert res.status_code == 401


async def test_register_throttle_locks_ip(client: AsyncClient) -> None:
    """注册重复失败锁定 IP：换新用户名同样 429。"""
    await client.post("/api/auth/register", json={"username": "dave", "password": "pass1234"})
    for _ in range(5):
        res = await client.post(
            "/api/auth/register", json={"username": "dave", "password": "pass1234"}
        )
        assert res.status_code == 409
    res = await client.post("/api/auth/register", json={"username": "erin", "password": "pass1234"})
    assert res.status_code == 429
