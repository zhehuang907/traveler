"""doctor 自检测试（所有外部请求用 respx mock，不打真实网络）。"""

import httpx
import pytest
import respx
from pydantic import SecretStr

from travel_agent.config import Settings
from travel_agent.doctor import (
    Package,
    Status,
    _sqlite_db_path,
    check_one_package,
    check_python,
    main,
    online_checks,
    run_checks,
)


def test_check_python() -> None:
    assert check_python().status == Status.OK


def test_missing_core_package_is_fail() -> None:
    result = check_one_package(Package("definitely-not-installed", "no_such_module_xyz"))
    assert result.status == Status.FAIL


def test_missing_optional_package_is_warn() -> None:
    result = check_one_package(
        Package("definitely-not-installed", "no_such_module_xyz", optional=True)
    )
    assert result.status == Status.WARN


def test_run_offline_checks_has_no_fail(settings: Settings) -> None:
    results = run_checks(settings, online=False)
    assert results, "应产出检查项"
    assert not any(r.status == Status.FAIL for r in results)
    # 未配置 Key 时 LLM 为 WARN
    llm = next(r for r in results if r.name == "密钥 LLM_API_KEY")
    assert llm.status == Status.WARN
    sqlite = next(r for r in results if r.name == "SQLite")
    assert sqlite.status == Status.OK


def test_sqlite_path_parser() -> None:
    path = _sqlite_db_path("sqlite+aiosqlite:///./data/app.db")
    assert path is not None and path.name == "app.db"
    assert _sqlite_db_path("postgresql+asyncpg://localhost/x") is None
    assert _sqlite_db_path("sqlite:///:memory:") is None


@pytest.fixture
def online_settings(settings: Settings) -> Settings:
    return Settings(
        app_env="test",
        cache_dir=settings.cache_dir,
        checkpoint_db=settings.checkpoint_db,
        database_url=settings.database_url,
        llm_api_key=SecretStr("sk-test"),
        llm_base_url="http://llm.example/v1",
    )


async def test_online_checks_success(online_settings: Settings) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=httpx.Response(200, json={"current_weather": {}})
        )
        mock.get("http://llm.example/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        results = await online_checks(online_settings)
    assert all(r.status == Status.OK for r in results)


async def test_online_checks_degrade_to_warn(online_settings: Settings) -> None:
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.open-meteo.com/v1/forecast").mock(
            side_effect=httpx.ConnectError("boom")
        )
        mock.get("http://llm.example/v1/models").mock(return_value=httpx.Response(401))
        results = await online_checks(online_settings)
    assert all(r.status == Status.WARN for r in results)


def test_run_checks_online_in_sync_context(settings: Settings) -> None:
    with respx.mock() as mock:
        mock.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=httpx.Response(200, json={})
        )
        results = run_checks(settings, online=True)
    assert any(r.name == "连通 Open-Meteo" for r in results)


def test_main_exit_zero() -> None:
    assert main([]) == 0


def test_main_bad_config_returns_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "")
    assert main([]) == 1
