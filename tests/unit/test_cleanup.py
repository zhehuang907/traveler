"""过期分享快照清理（纯文件操作，单元级，无需数据库）。"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from travel_agent.config import Settings
from travel_agent.services.cleanup import cleanup_expired_shares


def _write_snapshot(share_dir: Path, name: str, expires_at: str | None) -> None:
    (share_dir / name).write_text(
        json.dumps(
            {
                "plan": {"plan_id": "x"},
                "created_at": datetime.now(UTC).isoformat(),
                "expires_at": expires_at,
            }
        ),
        encoding="utf-8",
    )


def test_cleanup_removes_only_expired(tmp_path: Path) -> None:
    settings = Settings(app_env="test", data_dir=tmp_path)
    share = tmp_path / "share"
    share.mkdir()
    _write_snapshot(share, "expired.json", (datetime.now(UTC) - timedelta(days=1)).isoformat())
    _write_snapshot(share, "active.json", (datetime.now(UTC) + timedelta(days=1)).isoformat())
    _write_snapshot(share, "never.json", None)
    (share / "corrupt.json").write_text("{broken-json", encoding="utf-8")

    removed = cleanup_expired_shares(settings)

    assert removed == 1
    assert not (share / "expired.json").exists()
    assert (share / "active.json").exists()
    assert (share / "never.json").exists()
    assert (share / "corrupt.json").exists()


def test_cleanup_no_dir_is_noop(tmp_path: Path) -> None:
    settings = Settings(app_env="test", data_dir=tmp_path)
    assert cleanup_expired_shares(settings) == 0
