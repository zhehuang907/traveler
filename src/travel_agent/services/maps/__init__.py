"""地图能力包：高德 / OpenStreetMap 降级链。"""

from travel_agent.services.maps.amap import AMapProvider
from travel_agent.services.maps.base import MapsProvider
from travel_agent.services.maps.osm import OsmMapsProvider
from travel_agent.services.maps.service import CachedMapsProvider, MapsService

__all__ = [
    "AMapProvider",
    "CachedMapsProvider",
    "MapsProvider",
    "MapsService",
    "OsmMapsProvider",
]
