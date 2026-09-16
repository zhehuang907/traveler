"""Alembic 迁移环境（MySQL 主库）。

URL 解析优先级：alembic 命令行/程序注入 > 应用 Settings（async 驱动名替换为同步）。
- ``mysql+aiomysql`` → ``mysql+pymysql``（同步驱动跑迁移）
- ``sqlite+aiosqlite`` → ``sqlite``
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from travel_agent.db import models  # noqa: F401  # 导入以注册全部 ORM mapper
from travel_agent.db.base import Base

config = context.config


def _to_sync_url(url: str) -> str:
    if url.startswith("mysql+aiomysql"):
        return url.replace("+aiomysql", "+pymysql")
    if url.startswith("sqlite+aiosqlite"):
        return url.replace("+aiosqlite", "")
    return url


if not config.get_main_option("sqlalchemy.url"):
    # 延迟导入：脚本被 alembic 命令直接执行时也走应用配置
    from travel_agent.config import get_settings

    sync_url = _to_sync_url(get_settings().database_url)
    config.set_main_option("sqlalchemy.url", sync_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section: dict[str, str] = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
