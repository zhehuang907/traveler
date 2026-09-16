"""统一 httpx 异步客户端工厂：超时、UA、重定向策略一处管控。"""

import httpx

from travel_agent import __version__
from travel_agent.config import Settings

# Nominatim/Overpass 使用方政策要求可识别的 User-Agent
USER_AGENT = f"travel-agent/{__version__} (local travel planning agent)"


def build_client(settings: Settings, *, timeout: float | None = None) -> httpx.AsyncClient:
    """构造带统一超时与请求头的异步客户端，调用方用 ``async with`` 管理生命周期。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout or settings.external_timeout),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    )
