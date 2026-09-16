"""FastAPI 应用入口。

阶段五：完整可视化——SSE 流式、Jinja2 SSR 页面、行程/版本/分享 API。
``uvicorn travel_agent.main:app`` 即可启动。
"""

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import RequestResponseEndpoint

from travel_agent import __version__
from travel_agent.api import api_router
from travel_agent.api.errors import register_exception_handlers
from travel_agent.config import Settings, get_settings
from travel_agent.logging_conf import configure_logging, get_logger

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATE_DIR = Path(__file__).parent / "templates"

_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动：配置日志、确保运行期目录；关闭：释放数据库引擎。"""
    settings = app.state.settings
    configure_logging(settings.log_level, settings.effective_log_json)
    settings.ensure_dirs()
    log = get_logger(component="lifespan")
    log.info(
        "startup",
        app_env=settings.app_env,
        version=__version__,
        host=settings.host,
        port=settings.port,
    )
    try:
        yield
    finally:
        log.info("shutdown")
        engine = getattr(app.state, "engine", None)
        if engine is not None:
            await engine.dispose()


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        tokens = structlog.contextvars.bind_contextvars(request_id=request_id)
        log = get_logger(component="http")
        start = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("http_request_failed", method=request.method, path=request.url.path)
            raise
        else:
            duration_ms = round((perf_counter() - start) * 1000, 2)
            response.headers["x-request-id"] = request_id
            log.info(
                "http_access",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            return response
        finally:
            structlog.contextvars.reset_contextvars(request_id=tokens["request_id"])


def create_app(settings: Settings | None = None) -> FastAPI:
    """构造应用实例（测试可注入独立 Settings）。"""
    resolved = settings or get_settings()
    configure_logging(resolved.log_level, resolved.effective_log_json)
    resolved.ensure_dirs()

    app = FastAPI(
        title="智能旅游规划 Agent",
        version=__version__,
        description="自然语言 → 真实信息检索 → 天气/路线感知 → 可视化行程单 → 多轮修改",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_tags=[
            {"name": "auth", "description": "登录/注册/会话"},
            {"name": "chat", "description": "多轮对话与 SSE 流式事件"},
            {"name": "plan", "description": "行程、版本历史、CRUD、PDF 导出"},
            {"name": "share", "description": "只读分享快照"},
        ],
    )
    app.state.settings = resolved

    # 进程级单例数据库引擎：所有路由经 get_db 复用同一连接池
    from travel_agent.db import create_engine, make_session_factory

    engine = create_engine(resolved)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    _register_middleware(app)
    register_exception_handlers(app)
    app.include_router(api_router)
    app.mount("/static", StaticFiles(directory=_STATIC_DIR, check_dir=False), name="static")

    @app.get("/", include_in_schema=False)
    async def index(request: Request) -> HTMLResponse:
        return _templates.TemplateResponse(request, "index.html", {"version": __version__})

    @app.get("/plan/{plan_id}", include_in_schema=False)
    async def plan_page(request: Request, plan_id: str) -> HTMLResponse:
        return _templates.TemplateResponse(
            request, "plan.html", {"plan_id": plan_id, "version": __version__}
        )

    @app.get("/share/{token}", include_in_schema=False)
    async def share_page(request: Request, token: str) -> HTMLResponse:
        return _templates.TemplateResponse(
            request, "share.html", {"token": token, "version": __version__}
        )

    @app.get("/healthz", tags=["meta"])
    async def liveness() -> dict[str, str]:
        """存活探针：进程在跑即可。"""
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", tags=["meta"])
    async def readiness(request: Request) -> dict[str, object]:
        """就绪探针：运行期目录可写。"""
        settings: Settings = request.app.state.settings
        data_ok = os.access("data", os.W_OK)
        cache_ok = os.access(str(settings.cache_dir), os.W_OK)
        ready = data_ok and cache_ok
        return {
            "status": "ready" if ready else "degraded",
            "version": __version__,
            "checks": {
                "data_dir": "ok" if data_ok else "fail",
                "cache_dir": "ok" if cache_ok else "fail",
            },
        }

    return app


app = create_app()

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("travel_agent.main:app", host=get_settings().host, port=get_settings().port)
