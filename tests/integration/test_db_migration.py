"""Alembic 迁移冒烟：在独立迁移库上 upgrade head 建出全部业务表（方言无关）。"""

from collections.abc import Iterator
from pathlib import Path

import pymysql
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

_REPO_ROOT = Path(__file__).resolve().parents[2]

_MIG_DB = "travel_agent_migration"
_BASE = "mysql+pymysql://root:1234@127.0.0.1:3306"
_SYNC_URL = f"{_BASE}/{_MIG_DB}?charset=utf8mb4"

_EXPECTED_TABLES = {
    "plans",
    "plan_versions",
    "messages",
    "user_preferences",
    "users",
    "auth_sessions",
    "conversation_contexts",
}


@pytest.fixture(scope="module")
def mig_db() -> Iterator[str]:
    _exec(f"SET FOREIGN_KEY_CHECKS=0")
    _exec(f"DROP DATABASE IF EXISTS `{_MIG_DB}`")
    _exec(f"CREATE DATABASE `{_MIG_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    yield _SYNC_URL
    _exec(f"DROP DATABASE IF EXISTS `{_MIG_DB}`")


def _exec(sql: str) -> None:
    conn = pymysql.connect(host="127.0.0.1", port=3306, user="root", password="1234")
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def _alembic_config(url: str) -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_upgrade_head_creates_tables(mig_db: str) -> None:
    command.upgrade(_alembic_config(mig_db), "head")
    engine = create_engine(mig_db)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert _EXPECTED_TABLES <= names


def test_double_upgrade_is_idempotent(mig_db: str) -> None:
    cfg = _alembic_config(mig_db)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # 已在 head：无迁移可执行且不报错

    engine = create_engine(mig_db)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert _EXPECTED_TABLES <= names