"""checkpointer 序列化器与 Prompt 加载器测试。"""

from datetime import date, datetime, time

import pytest
from langchain_core.messages import HumanMessage

from travel_agent.agent.prompts import render_pair
from travel_agent.agent.serde import TypedJSONSerializer
from travel_agent.domain.brief import TravelBrief
from travel_agent.domain.models import DailyWeather, GeoPoint, Poi


def test_serde_round_trips_domain_models_and_messages() -> None:
    serde = TypedJSONSerializer()
    poi = Poi(
        poi_id="a1",
        name="武侯祠",
        location=GeoPoint(lat=30.0, lng=104.0),
        sources=["https://example.com/a"],
        provider="amap",
    )
    weather = {
        "2026-10-01": DailyWeather(
            date=date(2026, 10, 1),
            temp_min=10,
            temp_max=20,
            condition="晴",
            sunrise=time(6, 30),
        )
    }
    payload = {
        "poi": poi,
        "weather": weather,
        "messages": [HumanMessage(content="去成都")],
        "scalar": 3,
        "none": None,
    }
    tag, raw = serde.dumps_typed(payload)
    assert tag == "ta-json"
    restored = serde.loads_typed((tag, raw))
    assert isinstance(restored["poi"], Poi)
    assert restored["poi"].name == "武侯祠"
    assert str(restored["poi"].sources[0]) == "https://example.com/a"
    assert isinstance(restored["weather"]["2026-10-01"], DailyWeather)
    assert restored["weather"]["2026-10-01"].sunrise == time(6, 30)
    assert isinstance(restored["messages"][0], HumanMessage)
    assert restored["scalar"] == 3


def test_serde_rejects_non_whitelisted_type() -> None:
    serde = TypedJSONSerializer()
    raw = b'{"__ta_type__": "os.system", "__ta_data__": {"x": 1}}'
    with pytest.raises(ValueError, match="白名单"):
        serde.loads_typed(("ta-json", raw))


def test_serde_unknown_type_tag_and_unsupported_object() -> None:
    serde = TypedJSONSerializer()
    with pytest.raises(NotImplementedError):
        serde.loads_typed(("pickle", b""))
    assert serde.loads_typed(("null", b"")) is None
    with pytest.raises(TypeError):
        serde.dumps_typed({"x": object()})


def test_serde_bare_temporal_values_round_trip() -> None:
    serde = TypedJSONSerializer()
    payload = {
        "date": date(2026, 10, 1),
        "time": time(8, 30),
        "datetime": datetime(2026, 10, 1, 8, 30),
    }
    tag, raw = serde.dumps_typed(payload)
    restored = serde.loads_typed((tag, raw))
    assert restored["date"] == date(2026, 10, 1)
    assert restored["time"] == time(8, 30)
    assert restored["datetime"] == datetime(2026, 10, 1, 8, 30)


def test_serde_rejects_non_pydantic_tagged_class() -> None:
    serde = TypedJSONSerializer()
    raw = b'{"__ta_type__": "travel_agent.agent.serde.TypedJSONSerializer", "__ta_data__": {}}'
    with pytest.raises(ValueError, match="Pydantic"):
        serde.loads_typed(("ta-json", raw))


def test_serde_rejects_unknown_temporal_kind() -> None:
    serde = TypedJSONSerializer()
    raw = b'{"__ta_kind__": "week", "__ta_value__": "x"}'
    with pytest.raises(NotImplementedError):
        serde.loads_typed(("ta-json", raw))


def test_serde_revives_brief_with_dates() -> None:
    serde = TypedJSONSerializer()
    brief = TravelBrief(destination="成都", start_date=date(2026, 10, 1))
    tag, raw = serde.dumps_typed(brief)
    restored = serde.loads_typed((tag, raw))
    assert isinstance(restored, TravelBrief)
    assert restored.start_date == date(2026, 10, 1)


def test_render_pair_separates_system_and_user() -> None:
    system, user = render_pair(
        "clarify",
        missing_labels=["目的地"],
        destination="",
        message="想去玩",
    )
    assert "一个" in system
    assert "目的地" in user
    assert "{{" not in system and "{%" not in user


def test_render_pair_compose_context() -> None:
    system, user = render_pair(
        "compose",
        days=4,
        budget="5000",
        max_daily_hours=8,
        brief_json="{}",
        weather_text="晴",
        catalog_text="#1 武侯祠",
        web_text="- 贴士",
        transport_guide="用户默认公共交通出行。",
    )
    assert "4 天" in system
    assert "武侯祠" in user
    assert "公共交通" in user
