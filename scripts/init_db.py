"""初始化数据库：执行 Alembic upgrade head。

用法（项目根目录）：uv run python scripts/init_db.py
幂等：可重复执行。
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

from travel_agent.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _to_sync_url(url: str) -> str:
    """迁移走同步驱动：mysql+aiomysql→mysql+pymysql；sqlite+aiosqlite→sqlite。"""
    if url.startswith("mysql+aiomysql"):
        return url.replace("+aiomysql", "+pymysql")
    if url.startswith("sqlite+aiosqlite"):
        return url.replace("+aiosqlite", "")
    return url


def build_alembic_config() -> Config:
    """构造指向仓库根 alembic.ini 的配置，并同步驱动 URL（async -> sync）。"""
    settings = get_settings()
    settings.ensure_dirs()
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _to_sync_url(settings.database_url))
    return cfg


def main() -> None:
    command.upgrade(build_alembic_config(), "head")
    print("数据库初始化完成（upgrade head）。")


if __name__ == "__main__":
    main()
