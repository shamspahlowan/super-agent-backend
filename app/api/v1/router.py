from fastapi import APIRouter

from app.api.v1.anomalies import router as anomalies_router
from app.api.v1.balances import router as balances_router
from app.api.v1.data_quality import router as data_quality_router
from app.api.v1.health import router as health_router
from app.api.v1.incidents import router as incidents_router
from app.api.v1.liquidity import router as liquidity_router
from app.api.v1.replay import router as replay_router


api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(balances_router)
api_router.include_router(replay_router)
api_router.include_router(data_quality_router)
api_router.include_router(liquidity_router)
api_router.include_router(anomalies_router)
api_router.include_router(incidents_router)