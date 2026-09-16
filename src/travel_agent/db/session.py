"""异步引擎/会话工厂（MySQL 主库；CLI checkpointer 用独立 SQLite）。

DB-URL 由 ``Settings.database_url`` 提供。引擎为进程级单例（挂载在
``app.state``），所有路由经 ``get_db`` 依赖复用同一连接池，避免逐个端点
新建/销毁引擎（MySQL 下会产生连接风暴 / Too many connections）。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from travel_agent.config import Settings
from travel_agent.db.base import Base


def create_engine(settings: Settings) -> AsyncEngine:
    """创建异步引擎；MySQL 开启连接存活探活与回收。"""
    url = settings.database_url
    pool_kwargs: dict[str, object] = {}
    if url.startswith("mysql"):
        pool_kwargs.update(pool_pre_ping=True, pool_recycle=1800)
    elif url.startswith("sqlite"):
        pool_kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(url, **pool_kwargs)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """事务边界：正常提交，异常回滚。"""
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def create_schema(engine: AsyncEngine) -> None:
    """按元数据建表（测试/快速起步用；生产规范路径是 Alembic 迁移）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
