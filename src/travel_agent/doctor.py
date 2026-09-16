"""环境自检脚本。

用法：
    uv run python -m travel_agent.doctor            # 离线检查（默认）
    uv run python -m travel_agent.doctor --online   # 追加外部服务连通性检查

退出码：0 = 无致命问题（允许有 WARN）；1 = 存在 FAIL。
密钥只报告“已配置/未配置”，绝不打印值。
"""

import argparse
import asyncio
import importlib
import importlib.metadata
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NamedTuple

import httpx

from travel_agent import __version__
from travel_agent.config import Settings, get_settings

__all__ = ["main", "run_checks"]


class Status(Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    """单项检查结果。"""

    name: str
    status: Status
    detail: str


class Package(NamedTuple):
    dist: str
    module: str
    optional: bool = False


_CORE_PACKAGES = [
    Package("fastapi", "fastapi"),
    Package("uvicorn", "uvicorn"),
    Package("pydantic", "pydantic"),
    Package("pydantic-settings", "pydantic_settings"),
    Package("loguru", "loguru"),
    Package("structlog", "structlog"),
    Package("httpx", "httpx"),
    Package("tenacity", "tenacity"),
    Package("jinja2", "jinja2"),
    Package("diskcache", "diskcache"),
    Package("trafilatura", "trafilatura"),
    Package("sqlalchemy", "sqlalchemy"),
    Package("aiosqlite", "aiosqlite"),
    Package("alembic", "alembic"),
    Package("langgraph", "langgraph"),
    Package("langgraph-checkpoint-sqlite", "langgraph.checkpoint.sqlite"),
    Package("langchain-core", "langchain_core"),
    Package("langchain-openai", "langchain_openai"),
    Package("sse-starlette", "sse_starlette"),
    Package("duckduckgo-search", "duckduckgo_search"),
]
_OPTIONAL_PACKAGES = [Package("playwright", "playwright", optional=True)]


def check_python() -> CheckResult:
    """Python 版本必须 >= 3.11。

    用变量承载版本元组，避免静态检查器按本包 requires-python 把旧版本分支折叠掉，
    doctor 是独立于 uv 环境的版本守门员。
    """
    current = (sys.version_info.major, sys.version_info.minor)
    if current >= (3, 11):
        return CheckResult("Python", Status.OK, f"{current[0]}.{current[1]}")
    return CheckResult("Python", Status.FAIL, f"当前 {sys.version.split()[0]}，需要 3.11+")


def check_one_package(pkg: Package) -> CheckResult:
    name = f"依赖 {pkg.dist}"
    try:
        version = importlib.metadata.version(pkg.dist)
    except importlib.metadata.PackageNotFoundError:
        status = Status.WARN if pkg.optional else Status.FAIL
        hint = "可选依赖（PDF 导出）" if pkg.optional else "执行 uv sync 安装依赖"
        return CheckResult(name, status, f"未安装，{hint}")
    try:
        importlib.import_module(pkg.module)
    except ImportError as exc:
        # 元数据在但导入失败：通常是本机应用控制策略/原生库损坏，而非缺依赖
        return CheckResult(name, Status.WARN, f"已安装 {version}，但导入失败: {exc}")
    return CheckResult(name, Status.OK, version)


def _key_result(
    name: str, configured: bool, optional: bool, hint: str | None = None
) -> CheckResult:
    if configured:
        return CheckResult(f"密钥 {name}", Status.OK, "已配置")
    status = Status.WARN
    if hint is not None:
        detail = hint
    else:
        detail = (
            "未配置（可选，将自动降级）"
            if optional
            else "未配置（Agent 核心能力不可用，应用仍可启动）"
        )
    return CheckResult(f"密钥 {name}", status, detail)


def check_keys(settings: Settings) -> list[CheckResult]:
    """检查各外部服务 Key 的配置状态。"""
    return [
        _key_result("LLM_API_KEY", settings.llm_configured, optional=False),
        _key_result("TAVILY_API_KEY", settings.tavily_configured, optional=True),
        _key_result("BOCHA_API_KEY", settings.bocha_configured, optional=True),
        _key_result(
            "AMAP_API_KEY",
            settings.amap_configured,
            optional=True,
            hint="未配置（默认地图链仅高德且 OSM 端点不可达，地图/路线能力不可用）",
        ),
        _key_result("QWEATHER_API_KEY", settings.qweather_configured, optional=True),
        _key_result("LANGFUSE", settings.langfuse_configured, optional=True),
    ]


def check_env_file() -> CheckResult:
    exists = Path(".env").is_file()
    if exists:
        return CheckResult(".env", Status.OK, "存在")
    return CheckResult(
        ".env", Status.WARN, "不存在，可复制 .env.example 后填写（不影响默认配置启动）"
    )


def _probe_writable(path: Path) -> bool:
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".write_test_", delete=True):
            return True
    except OSError:
        return False


def check_dirs(settings: Settings) -> list[CheckResult]:
    settings.ensure_dirs()
    targets = {
        "数据目录 data": Path("data"),
        "缓存目录 cache": settings.cache_dir,
        "Checkpoint 目录": settings.checkpoint_db.parent,
    }
    results: list[CheckResult] = []
    for name, path in targets.items():
        if _probe_writable(path):
            results.append(CheckResult(name, Status.OK, str(path)))
        else:
            results.append(CheckResult(name, Status.FAIL, f"不可写: {path}"))
    return results


def _sqlite_db_path(database_url: str) -> Path | None:
    if ":///" not in database_url or database_url.startswith("postgresql"):
        return None
    suffix = database_url.split(":///", 1)[1]
    if ":memory:" in suffix:
        return None
    return Path(suffix)


def check_sqlite(settings: Settings) -> CheckResult:
    db_path = _sqlite_db_path(settings.database_url)
    if db_path is None:
        return CheckResult("SQLite", Status.OK, f"非 SQLite 后端: {settings.database_url}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("SELECT 1")
        return CheckResult("SQLite", Status.OK, str(db_path))
    except sqlite3.Error as exc:
        return CheckResult("SQLite", Status.FAIL, f"无法打开: {exc}")


async def check_mysql(settings: Settings) -> CheckResult:
    """主库为 MySQL 时校验可连通 3306 且能 SELECT 1。"""
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()
        return CheckResult("MySQL 3306", Status.OK, settings.database_url)
    except Exception as exc:
        return CheckResult("MySQL 3306", Status.FAIL, f"连接失败: {type(exc).__name__}: {exc}")


async def check_openmeteo() -> CheckResult:
    """免 Key 的天气兜底服务，作为外部网络基准探测。"""
    url = "https://api.open-meteo.com/v1/forecast"
    params: dict[str, str | float] = {
        "latitude": 30.57,
        "longitude": 104.07,
        "current_weather": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0)) as client:
            response = await client.get(url, params=params)
        if response.status_code == 200:
            return CheckResult("连通 Open-Meteo", Status.OK, "200（免 Key 天气兜底可用）")
        return CheckResult("连通 Open-Meteo", Status.WARN, f"HTTP {response.status_code}")
    except httpx.HTTPError as exc:
        return CheckResult("连通 Open-Meteo", Status.WARN, f"不可达: {type(exc).__name__}")


async def check_llm_endpoint(settings: Settings) -> CheckResult:
    if not settings.llm_configured:
        return CheckResult("连通 LLM", Status.WARN, "未配置 LLM_API_KEY，跳过")
    headers = {"Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0)) as client:
            response = await client.get(
                f"{settings.llm_base_url.rstrip('/')}/models", headers=headers
            )
    except httpx.HTTPError as exc:
        return CheckResult("连通 LLM", Status.WARN, f"不可达: {type(exc).__name__}")
    if response.status_code == 200:
        return CheckResult("连通 LLM", Status.OK, f"{settings.llm_base_url} 认证通过")
    if response.status_code in (401, 403):
        return CheckResult("连通 LLM", Status.WARN, f"HTTP {response.status_code}，Key 可能无效")
    return CheckResult(
        "连通 LLM", Status.WARN, f"HTTP {response.status_code}（部分厂商不开放 /models）"
    )


async def online_checks(settings: Settings) -> list[CheckResult]:
    results = await asyncio.gather(check_openmeteo(), check_llm_endpoint(settings))
    return list(results)


def run_checks(settings: Settings, online: bool = False) -> list[CheckResult]:
    """执行全部检查并返回结果列表（离线部分同步，在线部分异步聚合）。"""
    results: list[CheckResult] = [check_python()]
    results.extend(check_one_package(pkg) for pkg in _CORE_PACKAGES)
    results.extend(check_one_package(pkg) for pkg in _OPTIONAL_PACKAGES)
    results.append(check_env_file())
    results.extend(check_keys(settings))
    results.extend(check_dirs(settings))
    results.append(check_sqlite(settings))
    if settings.database_url.startswith("mysql"):
        results.append(asyncio.run(check_mysql(settings)))
    if online:
        results.extend(asyncio.run(online_checks(settings)))
    return results


def _format(results: list[CheckResult]) -> tuple[str, int, int, int]:
    ok_n = sum(r.status == Status.OK for r in results)
    warn_n = sum(r.status == Status.WARN for r in results)
    fail_n = sum(r.status == Status.FAIL for r in results)
    lines = [f"  [{r.status.value:^4}] {r.name} — {r.detail}" for r in results]
    return "\n".join(lines), ok_n, warn_n, fail_n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="travel-agent-doctor", description="环境自检")
    parser.add_argument("--online", action="store_true", help="追加外部服务连通性检查")
    args = parser.parse_args(argv)

    print(f"智能旅游规划 Agent v{__version__} 环境自检\n" + "=" * 56)
    try:
        settings = get_settings()
    except Exception as exc:
        print(f"  [FAIL] 配置加载 — {type(exc).__name__}: {exc}")
        print("        请检查 .env 格式（参照 .env.example）")
        return 1

    results = run_checks(settings, online=args.online)
    body, ok_n, warn_n, fail_n = _format(results)
    print(body)
    print("=" * 56)
    print(f"合计 {len(results)} 项：OK={ok_n}  WARN={warn_n}  FAIL={fail_n}")
    if fail_n:
        print("结论：存在致命问题，请先修复 FAIL 项。")
        return 1
    print("结论：核心环境就绪。" + (" 有可选项未配置，不影响启动。" if warn_n else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
