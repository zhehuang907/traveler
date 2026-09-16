"""接口层：聚合所有业务路由。"""

from fastapi import APIRouter

from travel_agent.api.routes_auth import router as auth_router
from travel_agent.api.routes_chat import router as chat_router
from travel_agent.api.routes_plan import router as plan_router
from travel_agent.api.routes_share import router as share_router

__all__ = ["api_router", "auth_router", "chat_router", "plan_router", "share_router"]

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(chat_router)
api_router.include_router(plan_router)
api_router.include_router(share_router)
