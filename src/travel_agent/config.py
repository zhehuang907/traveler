"""集中式配置：pydantic-settings 读取 .env 与环境变量，启动即完成类型校验。

所有密钥使用 SecretStr 承载，禁止在日志 / repr 中明文出现。
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AppEnv = Literal["dev", "prod", "test"]


class Settings(BaseSettings):
    """应用全部配置项，字段名小写即对应大写环境变量（如 LLM_API_KEY）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # ---- 应用 ----
    app_env: AppEnv = "dev"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    log_json: bool | None = None

    # ---- LLM（OpenAI 兼容协议） ----
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "deepseek-chat"
    llm_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=4096, ge=1, le=128_000)
    # 行程草稿等长文本生成单次可达数十秒，独立给宽松超时；
    # 不要与 EXTERNAL_TIMEOUT（外部工具接口 10s）混用，否则高峰期必然误杀
    llm_timeout: float = Field(default=120.0, gt=0.0, le=600.0)

    # ---- 搜索降级链 ----
    search_provider_chain: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["tavily", "bocha", "duckduckgo"]
    )
    tavily_api_key: SecretStr = SecretStr("")
    bocha_api_key: SecretStr = SecretStr("")

    # ---- 地图降级链 ----
    # 默认仅高德：目标部署环境无法访问 OSM 公共端点（Nominatim/Overpass/OSRM）。
    # 网络可达 OSM 的环境可设 MAPS_PROVIDER_CHAIN=amap,osm 恢复免 Key 兜底。
    maps_provider_chain: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["amap"])
    amap_api_key: SecretStr = SecretStr("")
    google_maps_api_key: SecretStr = SecretStr("")

    # ---- 天气降级链 ----
    weather_provider_chain: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["qweather", "openmeteo"]
    )
    qweather_api_key: SecretStr = SecretStr("")
    # 免费订阅开发者用 devapi 域名；付费/新项目在控制台查看专属 API Host
    qweather_api_host: str = "https://devapi.qweather.com"

    # ---- 外部 HTTP 调用（全链路 async，统一超时与重试） ----
    external_timeout: float = Field(default=10.0, gt=0.0, le=60.0)
    external_retry_attempts: int = Field(default=3, ge=1, le=6)
    external_retry_base_delay: float = Field(default=0.5, gt=0.0, le=10.0)
    # 检索扇出并发上限（信号量限流，缓存优先）
    fanout_concurrency: int = Field(default=4, ge=1, le=16)
    # 高德 POI 文本搜索配额仅 100 次/日：两次真实 POI 调用间的最小间隔（秒）
    poi_min_interval_s: float = Field(default=1.0, ge=0.0, le=10.0)

    # ---- 数据库（MySQL；CLI 的 checkpointer 仍用独立 SQLite 文件） ----
    database_url: str = "mysql+aiomysql://root:1234@127.0.0.1:3306/travel_agent?charset=utf8mb4"
    checkpoint_db: Path = Path("data/checkpoints.db")
    data_dir: Path = Path("data")

    # ---- 会话鉴权 ----
    # HttpOnly 会话 Cookie 名
    session_cookie_name: str = "session"
    # 会话有效期（秒），默认 7 天
    session_ttl_seconds: int = Field(default=604_800, ge=60)
    # 生产环境开启 Secure，测试/本地 http 关闭
    session_cookie_secure: bool = False
    # 登录/注册防爆破：窗口内失败次数达到阈值后锁定该用户名/IP 一段时间
    auth_max_failures: int = Field(default=5, ge=1, le=50)
    auth_lock_seconds: int = Field(default=900, ge=60, le=86_400)

    # ---- 缓存（秒） ----
    cache_dir: Path = Path("data/cache")
    cache_ttl_search: int = Field(default=21_600, ge=0)
    cache_ttl_poi: int = Field(default=86_400, ge=0)
    cache_ttl_weather: int = Field(default=3_600, ge=0)
    cache_ttl_route: int = Field(default=604_800, ge=0)

    # ---- 行程规则 ----
    max_daily_hours: float = Field(default=8.0, gt=0.0, le=24.0)
    max_revise_loops: int = Field(default=3, ge=0, le=10)
    default_currency: str = Field(default="CNY", min_length=3, max_length=3)

    # ---- 可观测性 ----
    langfuse_public_key: SecretStr = SecretStr("")
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "https://cloud.langfuse.com"

    # ---------- 校验器 ----------

    @field_validator(
        "search_provider_chain", "weather_provider_chain", "maps_provider_chain", mode="before"
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """环境变量里的 ``tavily,bocha`` 逗号串解析为列表。"""
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_level(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("LOG_LEVEL 不能为空")
        return value.strip().upper()

    @field_validator("log_json", mode="before")
    @classmethod
    def _empty_bool_as_none(cls, value: object) -> object:
        """`.env.example` 中留空的 ``LOG_JSON=`` 视为未设置（bool 不接受空字符串）。"""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("default_currency", mode="before")
    @classmethod
    def _upper_currency(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    # ---------- 便捷属性 ----------

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def effective_log_json(self) -> bool:
        """未显式设置 LOG_JSON 时，生产环境默认输出 JSON。"""
        return self.log_json if self.log_json is not None else self.is_prod

    @staticmethod
    def _has_secret(value: SecretStr) -> bool:
        return bool(value.get_secret_value())

    @property
    def llm_configured(self) -> bool:
        return self._has_secret(self.llm_api_key)

    @property
    def tavily_configured(self) -> bool:
        return self._has_secret(self.tavily_api_key)

    @property
    def bocha_configured(self) -> bool:
        return self._has_secret(self.bocha_api_key)

    @property
    def amap_configured(self) -> bool:
        return self._has_secret(self.amap_api_key)

    @property
    def qweather_configured(self) -> bool:
        return self._has_secret(self.qweather_api_key)

    @property
    def langfuse_configured(self) -> bool:
        return self._has_secret(self.langfuse_public_key) and self._has_secret(
            self.langfuse_secret_key
        )

    def ensure_dirs(self) -> None:
        """创建运行期目录（数据目录 / 缓存目录）。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例配置（测试可调用 ``get_settings.cache_clear()`` 复位）。"""
    return Settings()
