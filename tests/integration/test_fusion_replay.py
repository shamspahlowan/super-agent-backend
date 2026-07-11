from pathlib import Path

from app.data_quality.trust_score import FeedHealthEngine
from app.intelligence.anomaly_detection import (
    AnomalyDetectionEngine,
)
from app.intelligence.fusion import DecisionFusionEngine
from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
)
from app.ledger.balance_engine import BalanceEngine
from app.replay.controller import ReplayController
from app.replay.loader import SyntheticDataLoader
from app.schemas.incident import IncidentType


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

    fusion_engine = DecisionFusionEngine(
        liquidity_engine=liquidity_engine,
        anomaly_engine=anomaly_engine,
        feed_health_engine=feed_health_engine,
    )

    replay = ReplayController(
        events=events,
        opening_balances=bundle.opening_balances,
        balance_engine=balance_engine,
        feed_health_engine=feed_health_engine,
        liquidity_engine=liquidity_engine,
        anomaly_engine=anomaly_engine,
        fusion_engine=fusion_engine,
        agents=bundle.agents,
        context_events=bundle.context_events,
    )

    return replay, fusion_engine


def test_replay_creates_incident_for_ag003() -> None:
    replay, fusion_engine = build_system()

    replay.advance(314)

    incidents = fusion_engine.get_agent_incidents(
        "AG003",
        include_cleared=False,
    )

    assert incidents

    incident_types = {
        incident.incident_type
        for incident in incidents
    }

    assert incident_types.intersection(
        {
            IncidentType.UNUSUAL_ACTIVITY,
            IncidentType.COMBINED_PRIORITY,
        }
    )


def test_replay_incident_summary_is_updated() -> None:
    replay, fusion_engine = build_system()

    replay.advance(314)

    summary = fusion_engine.get_summary(
        as_of=replay.get_state().simulation_time,
    )

    assert summary.total_incidents > 0
    assert summary.active_incidents > 0


def test_replay_reset_clears_incident_history() -> None:
    replay, fusion_engine = build_system()

    replay.advance(314)

    assert fusion_engine.get_all_incidents()

    replay.reset()

    assert fusion_engine.get_all_incidents() == []