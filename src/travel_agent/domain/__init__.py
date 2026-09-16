"""领域内核：纯模型与规则，不依赖任何 IO，services/agent 都可向内引用。"""

from travel_agent.domain.brief import Pace, TravelBrief
from travel_agent.domain.draft import DraftDay, DraftItem, PlanDraft
from travel_agent.domain.models import (
    DailyWeather,
    DomainModel,
    GeoPoint,
    Poi,
    RouteInfo,
    SearchResult,
    TravelMode,
    WeatherForecast,
)
from travel_agent.domain.plan import (
    ItemCategory,
    PlanDay,
    PlanItem,
    TripPlan,
    new_plan_id,
)
from travel_agent.domain.plan_builder import (
    CandidateCatalog,
    CandidateEntry,
    HydrationResult,
    hydrate_plan,
)

__all__ = [
    "CandidateCatalog",
    "CandidateEntry",
    "DailyWeather",
    "DomainModel",
    "DraftDay",
    "DraftItem",
    "GeoPoint",
    "HydrationResult",
    "ItemCategory",
    "Pace",
    "PlanDay",
    "PlanDraft",
    "PlanItem",
    "Poi",
    "RouteInfo",
    "SearchResult",
    "TravelBrief",
    "TravelMode",
    "TripPlan",
    "WeatherForecast",
    "hydrate_plan",
    "new_plan_id",
]
