"""FastAPI 依赖测试。"""

from types import SimpleNamespace

from travel_agent.api.deps import get_request_id, get_settings
from travel_agent.config import Settings


def test_get_settings_returns_settings() -> None:
    assert isinstance(get_settings(), Settings)


def test_get_request_id_from_state() -> None:
    request = SimpleNamespace(state=SimpleNamespace(request_id="abc-123"))
    assert get_request_id(request) == "abc-123"  # type: ignore[arg-type]


def test_get_request_id_missing_falls_back() -> None:
    request = SimpleNamespace(state=SimpleNamespace())
    assert get_request_id(request) == "-"  # type: ignore[arg-type]
