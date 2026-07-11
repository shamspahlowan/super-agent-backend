from pathlib import Path

from app.data_quality.trust_score import FeedHealthEngine
from app.intelligence.anomaly_detection import (
    AnomalyDetectionEngine,
)
from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
)
from app.ledger.balance_engine import BalanceEngine
from app.replay.controller import ReplayController
from app.replay.loader import SyntheticDataLoader
from app.schemas.anomaly import AnomalyCategory


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

    anomaly_engine = AnomalyDetectionEngine(
        window_minutes=15,
        baseline_minutes=180,
        minimum_transactions=6,
        amount_tolerance_percent=2,
        medium_threshold=40,
        high_threshold=70,
    )

    replay = ReplayController(
        events=events,
        opening_balances=bundle.opening_balances,
        balance_engine=balance_engine,
        feed_health_engine=feed_health_engine,
        liquidity_engine=liquidity_engine,
        anomaly_engine=anomaly_engine,
        agents=bundle.agents,
        context_events=bundle.context_events,
    )

    return replay, anomaly_engine


def test_replay_detects_repeated_transaction_pattern() -> None:
    replay, anomaly_engine = build_system()

    # 09:00 to approximately 14:14
    replay.advance(314)

    result = anomaly_engine.get_agent_assessment(
        agent_id="AG003",
        as_of=replay.get_state().simulation_time,
    )

    assert result.requires_human_review is True

    factor_codes = {
        factor.code
        for factor in result.factors
    }

    assert "NEAR_IDENTICAL_AMOUNTS" in factor_codes
    assert "ACCOUNT_CONCENTRATION" in factor_codes


def test_replay_detects_cross_provider_pattern() -> None:
    replay, anomaly_engine = build_system()

    # 09:00 to 16:20
    replay.advance(440)

    result = anomaly_engine.get_agent_assessment(
        agent_id="AG001",
        as_of=replay.get_state().simulation_time,
    )

    assert (
        result.category
        == AnomalyCategory.CROSS_PROVIDER_REVIEW
    )

    assert any(
        factor.code == "CROSS_PROVIDER_LINK"
        for factor in result.factors
    )


def test_reset_clears_anomaly_observations() -> None:
    replay, anomaly_engine = build_system()

    replay.advance(314)
    replay.reset()

    result = anomaly_engine.get_agent_assessment(
        agent_id="AG003",
        as_of=replay.get_state().simulation_time,
    )

    assert result.transaction_count == 0
    assert result.requires_human_review is False
    assert result.score == 0