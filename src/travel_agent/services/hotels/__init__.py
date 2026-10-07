"""酒店推荐能力：Provider 协议与实现、服务层入口。"""

from travel_agent.services.hotels.amap import AMapHotelProvider
from travel_agent.services.hotels.base import HotelPriceProvider, HotelSearchProvider
from travel_agent.services.hotels.service import HotelService

__all__ = [
    "AMapHotelProvider",
    "HotelPriceProvider",
    "HotelSearchProvider",
    "HotelService",
]
