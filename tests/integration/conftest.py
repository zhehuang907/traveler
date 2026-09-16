"""Agent 集成测试夹具：关闭节流的注册表（外部服务全部为内存替身）。"""

import pytest
from agent_fakes import FakeMaps, FakeSearch, FakeWeather

from travel_agent.agent.tools.registry import ToolRegistry
from travel_agent.config import Settings


@pytest.fixture
def fast_settings(settings: Settings) -> Settings:
    """关闭 POI 节流，测试扇出不等待。"""
    return settings.model_copy(update={"poi_min_interval_s": 0.0})


@pytest.fixture
def registry(fast_settings: Settings) -> ToolRegistry:
    """全部外部服务都走内存替身的工具注册表。"""
    return ToolRegistry(
        fast_settings,
        search=FakeSearch(),
        maps=FakeMaps(),
        weather=FakeWeather(),
    )
