from fastapi import HTTPException, Request, status

from app.ledger.balance_engine import BalanceEngine
from app.replay.controller import ReplayController
from app.data_quality.trust_score import FeedHealthEngine

from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
)

def get_liquidity_engine(
    request: Request,
) -> LiquidityForecastEngine:
    engine = getattr(
        request.app.state,
        "liquidity_engine",
        None,
    )

    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Liquidity forecasting engine is not initialized.",
        )

    return engine

def get_balance_engine(
    request: Request,
) -> BalanceEngine:
    ledger = getattr(
        request.app.state,
        "balance_engine",
        None,
    )

    if ledger is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Balance ledger is not initialized.",
        )

    return ledger


def get_balance_engine(
    request: Request,
) -> BalanceEngine:
    engine = getattr(
        request.app.state,
        "balance_engine",
        None,
    )

    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Balance engine is not initialized.",
        )

    return engine


def get_replay_controller(
    request: Request,
) -> ReplayController:
    controller = getattr(
        request.app.state,
        "replay_controller",
        None,
    )

    if controller is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Replay controller is not initialized.",
        )

    return controller


def get_feed_health_engine(
    request: Request,
) -> FeedHealthEngine:
    engine = getattr(
        request.app.state,
        "feed_health_engine",
        None,
    )

    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feed-health engine is not initialized.",
        )

    return engine