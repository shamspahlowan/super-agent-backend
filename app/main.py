from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.base import Base
from app.db.session import engine
from app.ledger.balance_engine import BalanceEngine
from app.replay.loader import SyntheticDataLoader

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

    balance_engine = BalanceEngine(
        synthetic_bundle.opening_balances
    )

    app.state.synthetic_bundle = synthetic_bundle
    app.state.balance_engine = balance_engine
    app.state.replay_events = data_loader.build_event_stream(
        synthetic_bundle
    )

    logger.info(
        "Balance ledger initialized: agents=%s, transactions=%s",
        len(synthetic_bundle.agents),
        len(synthetic_bundle.transactions),
    )

    yield

    logger.info("Stopping %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Provider-aware decision-support API for shared-cash liquidity, "
        "provider-specific e-money pressure, unusual-activity review, "
        "data-quality uncertainty, replay, and case coordination."
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