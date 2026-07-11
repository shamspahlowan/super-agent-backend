from fastapi import APIRouter

from app.api.v1.data_quality import router as data_quality_router
from app.api.v1.balances import router as balances_router
from app.api.v1.health import router as health_router
from app.api.v1.replay import router as replay_router


api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(balances_router)
api_router.include_router(replay_router)
api_router.include_router(data_quality_router)