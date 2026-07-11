from pathlib import Path

from app.data_quality.trust_score import FeedHealthEngine
from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
)
from app.ledger.balance_engine import BalanceEngine
from app.replay.controller import ReplayController
from app.replay.loader import SyntheticDataLoader


ROOT_DIR = Path(__file__).resolve().parents[2]


def build_system():
    loader = SyntheticDataLoader(
        ROOT_DIR / "data" / "synthetic"
    )

    bundle = loader.load()
    events = loader.build_event_stream(bundle)

    balance_engine = BalanceEngine()

    feed_health_engine = FeedHealthEngine(
        stale_minutes=15,
        missing_minutes=30,
    )

    liquidity_engine = LiquidityForecastEngine(
        balance_engine=balance_engine,
        feed_health_engine=feed_health_engine,
        lookback_minutes=30,
        safety_buffer_percent=15,
        watch_minutes=120,
        critical_minutes=60,
        min_successful_transactions=3,
    )

    replay = ReplayController(
        events=events,
        opening_balances=bundle.opening_balances,
        balance_engine=balance_engine,
        feed_health_engine=feed_health_engine,
        liquidity_engine=liquidity_engine,
    )

    return replay, liquidity_engine


def test_replay_populates_liquidity_engine() -> None:
    replay, liquidity_engine = build_system()

    replay.advance(180)

    result = liquidity_engine.get_agent_forecast(
        agent_id="AG002",
        as_of=replay.get_state().simulation_time,
    )

    assert result.agent_id == "AG002"
    assert result.shared_cash.current_balance >= 0
    assert len(result.provider_forecasts) == 3


def test_reset_clears_liquidity_observations() -> None:
    replay, liquidity_engine = build_system()

    replay.advance(180)
    replay.reset()

    result = liquidity_engine.get_agent_forecast(
        agent_id="AG002",
        as_of=replay.get_state().simulation_time,
    )

    assert result.shared_cash.successful_transactions == 0