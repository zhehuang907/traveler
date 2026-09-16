"""天气相关纯函数测试：WMO 映射、日期解析、窗口校验、气候日期平移。"""

from datetime import date, timedelta

import pytest

from travel_agent.services.errors import OutsideForecastWindow
from travel_agent.services.search.base import parse_date
from travel_agent.services.weather.base import ensure_within_window
from travel_agent.services.weather.openmeteo import _parse_clock, _shift_year
from travel_agent.services.weather.wmo import describe_wmo


def test_wmo_known_codes_in_chinese() -> None:
    assert describe_wmo(0) == "晴"
    assert describe_wmo(61) == "小雨"
    assert describe_wmo(99) == "强雷暴伴冰雹"
    # Open-Meteo 偶尔以字符串回传代码
    assert describe_wmo("3") == "阴"


@pytest.mark.parametrize("code", [999, None, "", "暴雨", [], object()])
def test_wmo_unknown_falls_back(code: object) -> None:
    assert describe_wmo(code) == "未知"


def test_parse_date_variants() -> None:
    assert parse_date("2026-05-01") == date(2026, 5, 1)
    assert parse_date("2026-05-01T08:30:00Z") == date(2026, 5, 1)
    assert parse_date("2026-05-01T08:30:00+08:00") == date(2026, 5, 1)


@pytest.mark.parametrize("value", [None, "", "   ", "not-a-date", 12345])
def test_parse_date_invalid_returns_none(value: object) -> None:
    assert parse_date(value) is None


def test_ensure_within_window() -> None:
    today = date(2026, 10, 1)
    ensure_within_window("openmeteo", today + timedelta(days=15), today, horizon=16)
    with pytest.raises(OutsideForecastWindow, match="openmeteo"):
        ensure_within_window("openmeteo", today + timedelta(days=16), today, horizon=16)


def test_shift_year_normal_and_leap_day() -> None:
    assert _shift_year(date(2026, 10, 1)) == date(2025, 10, 1)
    # 2/29 前移到平年自动落到 2/28
    assert _shift_year(date(2024, 2, 29)) == date(2023, 2, 28)


def test_parse_clock() -> None:
    clock = _parse_clock("2026-10-01T06:10")
    assert clock is not None
    assert (clock.hour, clock.minute) == (6, 10)
    assert _parse_clock("06:10") is None
    assert _parse_clock(None) is None
