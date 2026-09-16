"""配置系统测试。"""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from travel_agent.config import Settings, get_settings


def test_defaults() -> None:
    settings = Settings(app_env="test")
    assert settings.search_provider_chain == ["tavily", "bocha", "duckduckgo"]
    assert settings.weather_provider_chain == ["qweather", "openmeteo"]
    assert settings.maps_provider_chain == ["amap"]
    assert settings.qweather_api_host == "https://devapi.qweather.com"
    assert settings.external_timeout == 10.0
    assert settings.external_retry_attempts == 3
    assert settings.external_retry_base_delay == 0.5
    assert settings.cache_ttl_search == 21_600
    assert settings.cache_ttl_poi == 86_400
    assert settings.cache_ttl_weather == 3_600
    assert settings.cache_ttl_route == 604_800
    assert settings.llm_base_url == "https://api.deepseek.com/v1"
    assert settings.llm_model == "deepseek-chat"
    assert settings.llm_timeout == 120.0
    assert settings.max_daily_hours == 8.0
    assert settings.default_currency == "CNY"
    assert settings.is_prod is False
    assert settings.effective_log_json is False


def test_prod_enables_json_logging_by_default() -> None:
    assert Settings(app_env="prod").effective_log_json is True
    assert Settings(app_env="prod", log_json=False).effective_log_json is False


def test_csv_provider_chain_from_string() -> None:
    # 字符串形态仅来自环境变量，走 model_validate 模拟真实解析路径
    settings = Settings.model_validate(
        {
            "app_env": "test",
            "search_provider_chain": " Tavily , DDG ",
            "weather_provider_chain": "openmeteo",
            "maps_provider_chain": " OSM ",
        }
    )
    assert settings.search_provider_chain == ["tavily", "ddg"]
    assert settings.weather_provider_chain == ["openmeteo"]
    assert settings.maps_provider_chain == ["osm"]


def test_provider_chain_accepts_list() -> None:
    settings = Settings(app_env="test", search_provider_chain=["a", "b"])
    assert settings.search_provider_chain == ["a", "b"]


def test_env_var_csv_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_PROVIDER_CHAIN", "tavily,duckduckgo")
    monkeypatch.setenv("LLM_API_KEY", "sk-secret")
    settings = Settings(app_env="test")
    get_settings.cache_clear()
    assert settings.search_provider_chain == ["tavily", "duckduckgo"]
    assert settings.llm_configured is True


def test_invalid_log_level() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="test", log_level="")


def test_empty_log_json_means_unset() -> None:
    # .env.example 中 LOG_JSON= 留空，复制即用不应报错
    assert Settings(app_env="test", log_json="").log_json is None
    assert Settings(app_env="test", log_json="  ").log_json is None
    assert Settings(app_env="test", log_json="true").log_json is True
    assert Settings(app_env="prod", log_json="false").effective_log_json is False


def test_invalid_temperature_and_port() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="test", llm_temperature=3.0)
    with pytest.raises(ValidationError):
        Settings(app_env="test", port=70000)
    with pytest.raises(ValidationError):
        Settings(app_env="test", llm_timeout=0)
    with pytest.raises(ValidationError):
        Settings(app_env="test", llm_timeout=601)


def test_currency_uppercased() -> None:
    assert Settings(app_env="test", default_currency="usd").default_currency == "USD"


def test_secret_not_leaked_in_repr_or_json() -> None:
    settings = Settings(app_env="test", llm_api_key=SecretStr("sk-supersecret"))
    assert "sk-supersecret" not in repr(settings)
    assert "sk-supersecret" not in settings.model_dump_json()
    assert settings.llm_api_key.get_secret_value() == "sk-supersecret"


def test_configured_flags() -> None:
    settings = Settings(
        app_env="test",
        tavily_api_key=SecretStr("t"),
        bocha_api_key=SecretStr("b"),
        amap_api_key=SecretStr("m"),
        qweather_api_key=SecretStr("q"),
    )
    assert settings.tavily_configured and settings.bocha_configured
    assert settings.amap_configured and settings.qweather_configured
    assert settings.llm_configured is False
    assert settings.langfuse_configured is False
    assert Settings(
        app_env="test",
        langfuse_public_key=SecretStr("p"),
        langfuse_secret_key=SecretStr("s"),
    ).langfuse_configured


def test_ensure_dirs(tmp_path: Path) -> None:
    settings = Settings(
        app_env="test",
        cache_dir=tmp_path / "cache",
        checkpoint_db=tmp_path / "sub" / "cp.db",
    )
    settings.ensure_dirs()
    assert (tmp_path / "cache").is_dir()
    assert (tmp_path / "sub").is_dir()


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    assert get_settings() is get_settings()
