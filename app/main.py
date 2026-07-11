from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.base import Base
from app.db.session import engine
from app.intelligence.liquidity_forecast import LiquidityForecastEngine
from app.ledger.balance_engine import BalanceEngine
from app.replay.controller import ReplayController
from app.replay.loader import SyntheticDataLoader
from app.data_quality.trust_score import FeedHealthEngine

from app.intelligence.anomaly_detection import (
    AnomalyDetectionEngine,
)
from app.intelligence.fusion import DecisionFusionEngine
from app.cases.service import CaseCoordinationService

import logging

from app.explanations.service import (
    GroundedExplanationService,
    OpenAIExplanationGenerator,
)


settings = get_settings()

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting %s", settings.app_name)

    Base.metadata.create_all(bind=engine)

    data_loader = SyntheticDataLoader(
        settings.transactions_file.parent
    )

    synthetic_bundle = data_loader.load()

    replay_events = data_loader.build_event_stream(
        synthetic_bundle
    )

    balance_engine = BalanceEngine()

    feed_health_engine = FeedHealthEngine(
    stale_minutes=settings.feed_stale_minutes,
    missing_minutes=settings.feed_missing_minutes,
    )

    liquidity_engine = LiquidityForecastEngine(
    balance_engine=balance_engine,
    feed_health_engine=feed_health_engine,
    lookback_minutes=settings.liquidity_lookback_minutes,
    safety_buffer_percent=(
        settings.liquidity_safety_buffer_percent
    ),
    watch_minutes=settings.liquidity_watch_minutes,
    critical_minutes=settings.liquidity_critical_minutes,
    min_successful_transactions=3,
    )

    anomaly_engine = AnomalyDetectionEngine(
    window_minutes=settings.anomaly_window_minutes,
    baseline_minutes=settings.anomaly_baseline_minutes,
    minimum_transactions=settings.anomaly_min_transactions,
    amount_tolerance_percent=(
        settings.anomaly_amount_tolerance_percent
    ),
    medium_threshold=settings.anomaly_medium_threshold,
    high_threshold=settings.anomaly_high_threshold,
    )

    fusion_engine = DecisionFusionEngine(
    liquidity_engine=liquidity_engine,
    anomaly_engine=anomaly_engine,
    feed_health_engine=feed_health_engine,
    )

    case_service = CaseCoordinationService()

    explanation_generator = None

    if (
        settings.openai_explanations_enabled
        and settings.openai_api_key is not None
    ):
        try:
            explanation_generator = (
                OpenAIExplanationGenerator(
                    api_key=(
                        settings.openai_api_key
                        .get_secret_value()
                    ),
                    model=(
                        settings
                        .openai_explanation_model
                    ),
                    timeout_seconds=(
                        settings
                        .openai_explanation_timeout_seconds
                    ),
                    max_output_tokens=(
                        settings
                        .openai_explanation_max_output_tokens
                    ),
                )
            )

            logger.info(
                "OpenAI explanation generator configured "
                "with model=%s",
                settings.openai_explanation_model,
            )

        except Exception:
            logger.exception(
                "OpenAI explanation initialization failed. "
                "Template fallback will remain active."
            )

    explanation_service = GroundedExplanationService(
        generator=explanation_generator,
        enabled=(
            settings.openai_explanations_enabled
        ),
    )

    replay_controller = ReplayController(
    events=replay_events,
    opening_balances=synthetic_bundle.opening_balances,
    balance_engine=balance_engine,
    feed_health_engine=feed_health_engine,
    liquidity_engine=liquidity_engine,
    anomaly_engine=anomaly_engine,
    fusion_engine=fusion_engine,
    case_service=case_service,
    agents=synthetic_bundle.agents,
    context_events=synthetic_bundle.context_events,
    )

    app.state.synthetic_bundle = synthetic_bundle
    app.state.balance_engine = balance_engine
    app.state.anomaly_engine = anomaly_engine
    app.state.feed_health_engine = feed_health_engine
    app.state.liquidity_engine = liquidity_engine
    app.state.fusion_engine = fusion_engine
    app.state.case_service = case_service
    app.state.replay_controller = replay_controller
    app.state.explanation_service = (
    explanation_service
    )

    logger.info(
        "Replay initialized successfully: agents=%s, events=%s",
        len(synthetic_bundle.agents),
        len(replay_events),
    )

    yield

    logger.info("Stopping %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Provider-aware decision-support API for liquidity, "
        "unusual activity, data quality and case coordination."
    ),
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.parsed_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    api_router,
    prefix=settings.api_v1_prefix,
)


@app.get("/", tags=["Root"])
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "documentation": "/docs",
        "health": f"{settings.api_v1_prefix}/health",
    }