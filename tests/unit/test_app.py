"""FastAPI 骨架测试：健康探针、请求 ID、统一错误信封。"""

from fastapi import Query
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient

from travel_agent.api.errors import AppError, ErrorCode
from travel_agent.config import Settings
from travel_agent.main import create_app


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert response.headers["x-request-id"]


async def test_request_id_echoed(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"x-request-id": "fixed-id"})
    assert response.headers["x-request-id"] == "fixed-id"


async def test_readiness(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"data_dir": "ok", "cache_dir": "ok"}


async def test_root_returns_page(client: AsyncClient) -> None:
    """阶段五：/ 返回 SSR 对话页面（不再重定向到 /docs）。"""
    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "小T" in response.text


async def test_404_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/no-such-path")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["request_id"]


async def test_validation_error_envelope(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/_needs_int")
    async def needs_int(value: int = Query(...)) -> dict[str, int]:
        return {"value": value}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        response = await ac.get("/_needs_int?value=abc")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"] is not None
    assert "fields" in error["details"]


async def test_app_error_envelope(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/_boom")
    async def boom() -> None:
        raise AppError(
            ErrorCode.LLM_NOT_CONFIGURED,
            "未配置 LLM",
            status_code=503,
            details={"hint": "填写 LLM_API_KEY"},
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        response = await ac.get("/_boom")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "LLM_NOT_CONFIGURED"
    assert error["details"] == {"hint": "填写 LLM_API_KEY"}


def test_unhandled_exception_envelope(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/_fatal")
    async def fatal() -> None:
        raise RuntimeError("内部细节不应外泄")

    with TestClient(app=app, raise_server_exceptions=False) as sync_client:
        response = sync_client.get("/_fatal")
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "INTERNAL_ERROR"
    assert "内部细节" not in response.text


async def test_openapi_tags(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/healthz" in paths  # 空业务路由不产生路径，但应用装配成功
