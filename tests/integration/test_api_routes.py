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
    res = await client.post(
        "/api/chat/upload", files={"file": ("trip.txt", b"Day1", "text/plain")}
    )
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
    res = await client.post(
        "/api/chat/upload", files={"file": ("trip.txt", b"Day1", "text/plain")}
    )
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
