"""LangGraph SQLite checkpointer 工厂（ADR-007：单机 SQLite）。

长连接随应用/CLI 生命周期管理：调用方通过异步上下文管理器取得 saver，
退出时关闭 aiosqlite 连接。测试向同结构的临时 db 路径打开即可。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from travel_agent.agent.serde import TypedJSONSerializer
from travel_agent.config import Settings


@asynccontextmanager
async def open_checkpointer(
    settings: Settings,
) -> AsyncIterator[AsyncSqliteSaver]:
    """打开（必要时建库建表）一个行程状态 checkpointer。"""
    db_path = Path(settings.checkpoint_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(db_path))
    try:
        saver = AsyncSqliteSaver(conn, serde=TypedJSONSerializer())
        await saver.setup()
        yield saver
    finally:
        await conn.close()
