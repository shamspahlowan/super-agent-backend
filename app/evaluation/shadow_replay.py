from __future__ import annotations

import csv
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from app.cases.service import (
    CaseCoordinationService,
)
from app.data_quality.trust_score import (
    FeedHealthEngine,
)
from app.evaluation.ground_truth import (
    GroundTruthBundle,
    GroundTruthScenario,
    load_ground_truth,
)
from app.evaluation.schemas import (
    ScenarioValidationResult,
    SimulationValidationReport,
    ValidationCheck,
    ValidationMetric,
    ValidationStatus,
)
from app.ingestion.canonical_event import ProviderID
from app.intelligence.anomaly_detection import (
    AnomalyDetectionEngine,
)
from app.intelligence.fusion import (
    DecisionFusionEngine,
)
from app.intelligence.liquidity_forecast import (
    LiquidityForecastEngine,
)
from app.ledger.balance_engine import BalanceEngine
from app.replay.controller import ReplayController
from app.replay.loader import SyntheticDataLoader
from app.schemas.anomaly import AnomalyCategory
from app.schemas.data_quality import (
    FeedHealthStatus,
)
from app.schemas.liquidity import LiquidityStatus


class ShadowReplayValidationError(RuntimeError):
    """Raised when the shadow replay cannot be completed."""


@dataclass
class ScenarioTracker:
    first_detected_at: datetime | None = None

    observations: dict[str, object] = field(
        default_factory=dict
    )

    flags: dict[str, bool] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class ShadowReplayConfiguration:
    feed_stale_minutes: int = 15
    feed_missing_minutes: int = 30

    liquidity_lookback_minutes: int = 30
    liquidity_safety_buffer_percent: float = 15
    liquidity_watch_minutes: int = 120
    liquidity_critical_minutes: int = 60

    anomaly_window_minutes: int = 15
    anomaly_baseline_minutes: int = 180
    anomaly_min_transactions: int = 6
    anomaly_amount_tolerance_percent: float = 2
    anomaly_medium_threshold: int = 40
    anomaly_high_threshold: int = 70


class ShadowReplayValidator:
    def __init__(
        self,
        *,
        synthetic_dir: str | Path,
        ground_truth_dir: str | Path,
        configuration: (
            ShadowReplayConfiguration | None
        ) = None,
    ) -> None:
        self.synthetic_dir = Path(
            synthetic_dir
        )

        self.ground_truth_dir = Path(
            ground_truth_dir
        )

        self.configuration = (
            configuration
            or ShadowReplayConfiguration()
        )

    def run(
        self,
    ) -> SimulationValidationReport:
        ground_truth = load_ground_truth(
            self.ground_truth_dir
        )

        loader = SyntheticDataLoader(
            self.synthetic_dir
        )

        bundle = loader.load()

        events = loader.build_event_stream(
            bundle
        )

        public_invariants = (
            self._validate_public_data(
                bundle=bundle,
                ground_truth=ground_truth,
            )
        )

        (
            replay,
            liquidity_engine,
            anomaly_engine,
            feed_health_engine,
            fusion_engine,
            case_service,
        ) = self._build_system(
            bundle=bundle,
            events=events,
        )

        trackers = {
            scenario.scenario_id: (
                ScenarioTracker()
            )
            for scenario
            in ground_truth.scenarios
        }

        event_latencies_ms: list[float] = []

        replay_started = time.perf_counter()

        while (
            replay.get_state().status.value
            != "COMPLETED"
        ):
            event_started = time.perf_counter()

            replay.step(1)

            elapsed_ms = (
                time.perf_counter()
                - event_started
            ) * 1000

            event_latencies_ms.append(
                elapsed_ms
            )

            as_of = (
                replay.get_state()
                .simulation_time
            )

            for scenario in ground_truth.scenarios:
                self._observe_scenario(
                    scenario=scenario,
                    tracker=trackers[
                        scenario.scenario_id
                    ],
                    as_of=as_of,
                    liquidity_engine=(
                        liquidity_engine
                    ),
                    anomaly_engine=(
                        anomaly_engine
                    ),
                    feed_health_engine=(
                        feed_health_engine
                    ),
                )

        replay_duration_seconds = round(
            time.perf_counter()
            - replay_started,
            4,
        )

        final_time = (
            replay.get_state().simulation_time
        )

        for scenario in ground_truth.scenarios:
            self._observe_scenario(
                scenario=scenario,
                tracker=trackers[
                    scenario.scenario_id
                ],
                as_of=final_time,
                liquidity_engine=liquidity_engine,
                anomaly_engine=anomaly_engine,
                feed_health_engine=(
                    feed_health_engine
                ),
            )

        scenario_results = [
            self._build_scenario_result(
                scenario=scenario,
                tracker=trackers[
                    scenario.scenario_id
                ],
            )
            for scenario in ground_truth.scenarios
        ]

        metrics = self._build_metrics(
            scenario_results=scenario_results,
            trackers=trackers,
            event_latencies_ms=(
                event_latencies_ms
            ),
            fusion_engine=fusion_engine,
            case_service=case_service,
        )

        all_invariants_passed = all(
            check.status
            != ValidationStatus.FAIL
            for check in public_invariants
        )

        all_scenarios_passed = all(
            result.passed
            for result in scenario_results
        )

        required_metric_names = {
            "anomaly_precision",
            "anomaly_recall",
            "hard_negative_false_positive_rate",
            "data_quality_detection_coverage",
            "liquidity_detection_coverage",
            "incident_explanation_coverage",
            "incident_case_coverage",
        }

        required_metrics_passed = all(
            metric.passed
            for metric in metrics
            if metric.name
            in required_metric_names
        )

        overall_passed = (
            all_invariants_passed
            and all_scenarios_passed
            and required_metrics_passed
        )

        return SimulationValidationReport(
            run_id=str(
                ground_truth.manifest.get(
                    "run_id",
                    "UNKNOWN",
                )
            ),
            seed=int(
                ground_truth.manifest.get(
                    "seed",
                    0,
                )
            ),
            generated_at=datetime.now(
                timezone.utc
            ),
            synthetic_directory=str(
                self.synthetic_dir.resolve()
            ),
            ground_truth_directory=str(
                self.ground_truth_dir.resolve()
            ),
            public_invariants=public_invariants,
            scenario_results=scenario_results,
            metrics=metrics,
            total_replay_events=len(events),
            replay_duration_seconds=(
                replay_duration_seconds
            ),
            overall_status=(
                ValidationStatus.PASS
                if overall_passed
                else ValidationStatus.FAIL
            ),
            overall_passed=overall_passed,
            notes=[
                (
                    "Hidden scenario labels were loaded only by "
                    "the evaluator after public event ingestion."
                ),
                (
                    "No scenario_id was provided to the balance, "
                    "forecast, anomaly, fusion or case engines."
                ),
                (
                    "Risk outputs remain advisory and do not "
                    "perform financial actions."
                ),
            ],
        )

    def _build_system(
        self,
        *,
        bundle,
        events,
    ):
        config = self.configuration

        balance_engine = BalanceEngine()

        feed_health_engine = FeedHealthEngine(
            stale_minutes=(
                config.feed_stale_minutes
            ),
            missing_minutes=(
                config.feed_missing_minutes
            ),
        )

        liquidity_engine = (
            LiquidityForecastEngine(
                balance_engine=balance_engine,
                feed_health_engine=(
                    feed_health_engine
                ),
                lookback_minutes=(
                    config
                    .liquidity_lookback_minutes
                ),
                safety_buffer_percent=(
                    config
                    .liquidity_safety_buffer_percent
                ),
                watch_minutes=(
                    config.liquidity_watch_minutes
                ),
                critical_minutes=(
                    config
                    .liquidity_critical_minutes
                ),
                min_successful_transactions=3,
            )
        )

        anomaly_engine = (
            AnomalyDetectionEngine(
                window_minutes=(
                    config
                    .anomaly_window_minutes
                ),
                baseline_minutes=(
                    config
                    .anomaly_baseline_minutes
                ),
                minimum_transactions=(
                    config
                    .anomaly_min_transactions
                ),
                amount_tolerance_percent=(
                    config
                    .anomaly_amount_tolerance_percent
                ),
                medium_threshold=(
                    config
                    .anomaly_medium_threshold
                ),
                high_threshold=(
                    config
                    .anomaly_high_threshold
                ),
            )
        )

        fusion_engine = DecisionFusionEngine(
            liquidity_engine=liquidity_engine,
            anomaly_engine=anomaly_engine,
            feed_health_engine=(
                feed_health_engine
            ),
        )

        case_service = (
            CaseCoordinationService()
        )

        replay = ReplayController(
            events=events,
            opening_balances=(
                bundle.opening_balances
            ),
            balance_engine=balance_engine,
            feed_health_engine=(
                feed_health_engine
            ),
            liquidity_engine=liquidity_engine,
            anomaly_engine=anomaly_engine,
            fusion_engine=fusion_engine,
            case_service=case_service,
            agents=bundle.agents,
            context_events=(
                bundle.context_events
            ),
        )

        return (
            replay,
            liquidity_engine,
            anomaly_engine,
            feed_health_engine,
            fusion_engine,
            case_service,
        )

    def _observe_scenario(
        self,
        *,
        scenario: GroundTruthScenario,
        tracker: ScenarioTracker,
        as_of: datetime,
        liquidity_engine: (
            LiquidityForecastEngine
        ),
        anomaly_engine: (
            AnomalyDetectionEngine
        ),
        feed_health_engine: (
            FeedHealthEngine
        ),
    ) -> None:
        if as_of < scenario.start_time:
            return

        label = scenario.label

        if (
            label
            == "HIDDEN_PROVIDER_SHORTAGE"
        ):
            self._observe_hidden_shortage(
                scenario=scenario,
                tracker=tracker,
                as_of=as_of,
                liquidity_engine=(
                    liquidity_engine
                ),
            )

        elif (
            label
            == "REPEATED_CASH_OUT_CLUSTER"
        ):
            self._observe_repeated_cluster(
                scenario=scenario,
                tracker=tracker,
                as_of=as_of,
                anomaly_engine=anomaly_engine,
            )

        elif (
            label
            == "LEGITIMATE_DEMAND_SPIKE"
        ):
            self._observe_legitimate_demand(
                scenario=scenario,
                tracker=tracker,
                as_of=as_of,
                anomaly_engine=anomaly_engine,
            )

        elif (
            label
            == "CROSS_PROVIDER_LINKED_ACTIVITY"
        ):
            self._observe_cross_provider(
                scenario=scenario,
                tracker=tracker,
                as_of=as_of,
                anomaly_engine=anomaly_engine,
            )

        elif (
            label
            == "FEED_DELAY_RECOVERY"
        ):
            self._observe_feed_delay(
                scenario=scenario,
                tracker=tracker,
                as_of=as_of,
                feed_health_engine=(
                    feed_health_engine
                ),
            )

        elif label == "BALANCE_CONFLICT":
            self._observe_balance_conflict(
                scenario=scenario,
                tracker=tracker,
                as_of=as_of,
                feed_health_engine=(
                    feed_health_engine
                ),
            )

    @staticmethod
    def _observe_hidden_shortage(
        *,
        scenario: GroundTruthScenario,
        tracker: ScenarioTracker,
        as_of: datetime,
        liquidity_engine: LiquidityForecastEngine,
    ) -> None:
        if scenario.provider_id is None:
            return

        forecast = liquidity_engine.get_agent_forecast(
            agent_id=scenario.agent_id,
            as_of=as_of,
        )

        provider_forecast = next(
            (
                item
                for item in forecast.provider_forecasts
                if item.provider_id
                == scenario.provider_id
            ),
            None,
        )

        if provider_forecast is None:
            return

        risky = provider_forecast.status in {
            LiquidityStatus.WATCH,
            LiquidityStatus.CRITICAL,
            LiquidityStatus.DEPLETED,
        }

        snapshot = {
            "provider_status": (
                provider_forecast.status.value
            ),
            "shared_cash_status": (
                forecast.shared_cash.status.value
            ),
            "hidden_provider_shortage": (
                forecast.hidden_provider_shortage
            ),
            "current_provider_balance": str(
                provider_forecast.current_balance
            ),
            "net_depletion_per_minute": str(
                provider_forecast
                .net_depletion_per_minute
            ),
            "minutes_to_depletion": (
                provider_forecast
                .minutes_to_depletion
            ),
            "minutes_to_safety_threshold": (
                provider_forecast
                .minutes_to_safety_threshold
            ),
            "confidence": (
                provider_forecast.confidence
            ),
        }

        # Keep updating before detection, but freeze the useful
        # observation snapshot after the first actual detection.
        if tracker.first_detected_at is None:
            tracker.observations.update(snapshot)

        if not risky:
            return

        tracker.flags[
            "provider_pressure_detected"
        ] = True

        if forecast.hidden_provider_shortage:
            tracker.flags[
                "hidden_shortage_detected"
            ] = True

        if tracker.first_detected_at is None:
            tracker.first_detected_at = as_of

            tracker.observations.update(snapshot)

    @staticmethod
    def _observe_repeated_cluster(
        *,
        scenario: GroundTruthScenario,
        tracker: ScenarioTracker,
        as_of: datetime,
        anomaly_engine: AnomalyDetectionEngine,
    ) -> None:
        assessment = (
            anomaly_engine.get_agent_assessment(
                agent_id=scenario.agent_id,
                as_of=as_of,
            )
        )

        factor_codes = {
            factor.code
            for factor in assessment.factors
        }

        near_identical = (
            "NEAR_IDENTICAL_AMOUNTS"
            in factor_codes
        )

        concentrated = (
            "ACCOUNT_CONCENTRATION"
            in factor_codes
        )

        detected = (
            assessment.requires_human_review
            and near_identical
            and concentrated
        )

        snapshot = {
            "score": assessment.score,
            "band": assessment.band.value,
            "category": assessment.category.value,
            "factor_codes": sorted(
                factor_codes
            ),
            "transaction_count": (
                assessment.transaction_count
            ),
            "unique_accounts": (
                assessment.unique_accounts
            ),
            "repeated_amount_ratio": (
                assessment.repeated_amount_ratio
            ),
            "dominant_account_ratio": (
                assessment.dominant_account_ratio
            ),
        }

        if tracker.first_detected_at is None:
            tracker.observations.update(snapshot)

        if near_identical:
            tracker.flags[
                "near_identical_detected"
            ] = True

        if concentrated:
            tracker.flags[
                "concentration_detected"
            ] = True

        if detected:
            tracker.flags[
                "review_required"
            ] = True

            if tracker.first_detected_at is None:
                tracker.first_detected_at = as_of
                tracker.observations.update(snapshot)

    @staticmethod
    def _observe_legitimate_demand(
        *,
        scenario: GroundTruthScenario,
        tracker: ScenarioTracker,
        as_of: datetime,
        anomaly_engine: AnomalyDetectionEngine,
    ) -> None:
        """
        Evaluate the legitimate-demand scenario only inside its
        intended evaluation window.

        The final assessment is calculated at scenario.end_time so
        later normal activity cannot overwrite the result.
        """

        if as_of < scenario.expected_detection_after:
            return

        # During the active scenario, continuously check that the
        # demand spike is not incorrectly classified for review.
        if as_of <= scenario.end_time:
            assessment = anomaly_engine.get_agent_assessment(
                agent_id=scenario.agent_id,
                as_of=as_of,
            )

            if assessment.requires_human_review:
                tracker.flags[
                    "false_positive_observed"
                ] = True

                if tracker.first_detected_at is None:
                    tracker.first_detected_at = as_of

            if (
                assessment.category
                == AnomalyCategory.LEGITIMATE_DEMAND_SPIKE
            ):
                tracker.flags[
                    "legitimate_category_observed"
                ] = True

            tracker.observations.update(
                {
                    "evaluation_time": as_of.isoformat(),
                    "score": assessment.score,
                    "band": assessment.band.value,
                    "category": assessment.category.value,
                    "requires_human_review": (
                        assessment.requires_human_review
                    ),
                    "active_contexts": (
                        assessment.active_contexts
                    ),
                    "unique_accounts": (
                        assessment.unique_accounts
                    ),
                    "unique_providers": (
                        assessment.unique_providers
                    ),
                }
            )

            return

        # The first replay event after the scenario ends performs one
        # deterministic final assessment at the exact end timestamp.
        if tracker.flags.get(
            "final_evaluation_completed",
            False,
        ):
            return

        final_assessment = (
            anomaly_engine.get_agent_assessment(
                agent_id=scenario.agent_id,
                as_of=scenario.end_time,
            )
        )

        tracker.flags[
            "final_evaluation_completed"
        ] = True

        if (
            final_assessment.category
            == AnomalyCategory.LEGITIMATE_DEMAND_SPIKE
        ):
            tracker.flags[
                "legitimate_category_observed"
            ] = True

        tracker.observations.update(
            {
                "final_evaluation_time": (
                    scenario.end_time.isoformat()
                ),
                "final_score": final_assessment.score,
                "final_band": (
                    final_assessment.band.value
                ),
                "final_category": (
                    final_assessment.category.value
                ),
                "requires_human_review": (
                    final_assessment
                    .requires_human_review
                ),
                "active_contexts": (
                    final_assessment.active_contexts
                ),
                "unique_accounts": (
                    final_assessment.unique_accounts
                ),
                "unique_providers": (
                    final_assessment.unique_providers
                ),
            }
        )

    @staticmethod
    def _observe_cross_provider(
        *,
        scenario: GroundTruthScenario,
        tracker: ScenarioTracker,
        as_of: datetime,
        anomaly_engine: AnomalyDetectionEngine,
    ) -> None:
        assessment = (
            anomaly_engine.get_agent_assessment(
                agent_id=scenario.agent_id,
                as_of=as_of,
            )
        )

        factor_codes = {
            factor.code
            for factor in assessment.factors
        }

        detected = (
            "CROSS_PROVIDER_LINK"
            in factor_codes
            and assessment.requires_human_review
        )

        snapshot = {
            "score": assessment.score,
            "band": assessment.band.value,
            "category": assessment.category.value,
            "provider_scope": [
                provider.value
                for provider
                in assessment.provider_scope
            ],
            "factor_codes": sorted(
                factor_codes
            ),
            "transaction_count": (
                assessment.transaction_count
            ),
            "unique_providers": (
                assessment.unique_providers
            ),
            "unique_accounts": (
                assessment.unique_accounts
            ),
        }

        if tracker.first_detected_at is None:
            tracker.observations.update(snapshot)

        if detected:
            tracker.flags[
                "cross_provider_detected"
            ] = True

            if tracker.first_detected_at is None:
                tracker.first_detected_at = as_of
                tracker.observations.update(snapshot)

    @staticmethod
    def _observe_feed_delay(
        *,
        scenario: GroundTruthScenario,
        tracker: ScenarioTracker,
        as_of: datetime,
        feed_health_engine: (
            FeedHealthEngine
        ),
    ) -> None:
        if scenario.provider_id is None:
            return

        health = (
            feed_health_engine
            .get_feed_health(
                agent_id=scenario.agent_id,
                provider_id=(
                    scenario.provider_id
                ),
                as_of=as_of,
            )
        )

        tracker.observations.update(
            {
                "latest_status": (
                    health.status.value
                ),
                "latest_confidence": (
                    health.confidence
                ),
                "data_age_minutes": (
                    health.data_age_minutes
                ),
            }
        )

        if (
            scenario.start_time
            <= as_of
            < scenario.end_time
            and health.status
            in {
                FeedHealthStatus.STALE,
                FeedHealthStatus.MISSING,
            }
        ):
            tracker.flags[
                "unhealthy_feed_observed"
            ] = True

            if tracker.first_detected_at is None:
                tracker.first_detected_at = as_of

        if (
            as_of >= scenario.end_time
            and health.status
            == FeedHealthStatus.HEALTHY
        ):
            tracker.flags[
                "recovery_observed"
            ] = True

    @staticmethod
    def _observe_balance_conflict(
        *,
        scenario: GroundTruthScenario,
        tracker: ScenarioTracker,
        as_of: datetime,
        feed_health_engine: (
            FeedHealthEngine
        ),
    ) -> None:
        if scenario.provider_id is None:
            return

        health = (
            feed_health_engine
            .get_feed_health(
                agent_id=scenario.agent_id,
                provider_id=(
                    scenario.provider_id
                ),
                as_of=as_of,
            )
        )

        tracker.observations.update(
            {
                "latest_status": (
                    health.status.value
                ),
                "reported_balance": (
                    str(
                        health.reported_balance
                    )
                    if health.reported_balance
                    is not None
                    else None
                ),
                "calculated_balance": (
                    str(
                        health.calculated_balance
                    )
                    if health.calculated_balance
                    is not None
                    else None
                ),
                "balance_difference": (
                    str(
                        health.balance_difference
                    )
                    if health.balance_difference
                    is not None
                    else None
                ),
            }
        )

        if (
            health.status
            == FeedHealthStatus.CONFLICTING
        ):
            tracker.flags[
                "conflict_detected"
            ] = True

            if tracker.first_detected_at is None:
                tracker.first_detected_at = as_of

    def _build_scenario_result(
        self,
        *,
        scenario: GroundTruthScenario,
        tracker: ScenarioTracker,
    ) -> ScenarioValidationResult:
        checks: list[ValidationCheck] = []

        if (
            scenario.label
            == "HIDDEN_PROVIDER_SHORTAGE"
        ):
            checks.extend(
                [
                    self._boolean_check(
                        name=(
                            "provider_pressure_detected"
                        ),
                        passed=tracker.flags.get(
                            "provider_pressure_detected",
                            False,
                        ),
                        expected=(
                            "Provider forecast reaches "
                            "WATCH, CRITICAL or DEPLETED."
                        ),
                    ),
                    self._boolean_check(
                        name=(
                            "hidden_provider_shortage"
                        ),
                        passed=tracker.flags.get(
                            "hidden_shortage_detected",
                            False,
                        ),
                        expected=(
                            "Shared cash remains healthier "
                            "than provider e-money."
                        ),
                    ),
                ]
            )

        elif (
            scenario.label
            == "REPEATED_CASH_OUT_CLUSTER"
        ):
            checks.extend(
                [
                    self._boolean_check(
                        name=(
                            "near_identical_amounts"
                        ),
                        passed=tracker.flags.get(
                            "near_identical_detected",
                            False,
                        ),
                        expected=(
                            "Near-identical amount factor "
                            "is present."
                        ),
                    ),
                    self._boolean_check(
                        name=(
                            "account_concentration"
                        ),
                        passed=tracker.flags.get(
                            "concentration_detected",
                            False,
                        ),
                        expected=(
                            "Account concentration factor "
                            "is present."
                        ),
                    ),
                    self._boolean_check(
                        name="human_review",
                        passed=tracker.flags.get(
                            "review_required",
                            False,
                        ),
                        expected=(
                            "Assessment requires human review."
                        ),
                    ),
                ]
            )

        elif (
            scenario.label
            == "LEGITIMATE_DEMAND_SPIKE"
        ):
            no_false_positive = not (
                tracker.flags.get(
                    "false_positive_observed",
                    False,
                )
            )

            checks.extend(
                [
                    self._boolean_check(
                        name=(
                            "no_false_positive"
                        ),
                        passed=no_false_positive,
                        expected=(
                            "Known broad demand spike does "
                            "not require risk review."
                        ),
                    ),
                    self._boolean_check(
                        name=(
                            "context_category"
                        ),
                        passed=tracker.flags.get(
                            "legitimate_category_observed",
                            False,
                        ),
                        expected=(
                            "Final category is "
                            "LEGITIMATE_DEMAND_SPIKE."
                        ),
                    ),
                ]
            )

        elif (
            scenario.label
            == "CROSS_PROVIDER_LINKED_ACTIVITY"
        ):
            checks.append(
                self._boolean_check(
                    name=(
                        "cross_provider_link"
                    ),
                    passed=tracker.flags.get(
                        "cross_provider_detected",
                        False,
                    ),
                    expected=(
                        "Cross-provider linked-account "
                        "factor requires review."
                    ),
                )
            )

        elif (
            scenario.label
            == "FEED_DELAY_RECOVERY"
        ):
            checks.extend(
                [
                    self._boolean_check(
                        name=(
                            "feed_degradation"
                        ),
                        passed=tracker.flags.get(
                            "unhealthy_feed_observed",
                            False,
                        ),
                        expected=(
                            "Feed becomes STALE or MISSING."
                        ),
                    ),
                    self._boolean_check(
                        name="feed_recovery",
                        passed=tracker.flags.get(
                            "recovery_observed",
                            False,
                        ),
                        expected=(
                            "Feed returns to HEALTHY "
                            "after recovery."
                        ),
                    ),
                ]
            )

        elif (
            scenario.label
            == "BALANCE_CONFLICT"
        ):
            checks.append(
                self._boolean_check(
                    name="balance_conflict",
                    passed=tracker.flags.get(
                        "conflict_detected",
                        False,
                    ),
                    expected=(
                        "Feed health becomes CONFLICTING."
                    ),
                )
            )

        else:
            checks.append(
                ValidationCheck(
                    name="known_scenario_type",
                    status=ValidationStatus.FAIL,
                    expected=(
                        "A supported scenario label."
                    ),
                    observed=scenario.label,
                    message=(
                        "No validator exists for this "
                        "scenario type."
                    ),
                )
            )

        passed = all(
            check.status
            != ValidationStatus.FAIL
            for check in checks
        )

        detection_delay = None

        if tracker.first_detected_at is not None:
            detection_delay = round(
                (
                    tracker.first_detected_at
                    - scenario
                    .expected_detection_after
                ).total_seconds()
                / 60,
                2,
            )

        return ScenarioValidationResult(
            scenario_id=scenario.scenario_id,
            label=scenario.label,
            agent_id=scenario.agent_id,
            provider_id=scenario.provider_id,
            expected_detection_after=(
                scenario
                .expected_detection_after
            ),
            first_detected_at=(
                tracker.first_detected_at
            ),
            detection_delay_minutes=(
                detection_delay
            ),
            status=(
                ValidationStatus.PASS
                if passed
                else ValidationStatus.FAIL
            ),
            passed=passed,
            checks=checks,
            observations=tracker.observations,
        )

    @staticmethod
    def _boolean_check(
        *,
        name: str,
        passed: bool,
        expected: str,
    ) -> ValidationCheck:
        return ValidationCheck(
            name=name,
            status=(
                ValidationStatus.PASS
                if passed
                else ValidationStatus.FAIL
            ),
            expected=expected,
            observed=(
                "Observed"
                if passed
                else "Not observed"
            ),
            message=(
                "Validation condition passed."
                if passed
                else "Expected condition was not detected."
            ),
        )

    def _validate_public_data(
        self,
        *,
        bundle,
        ground_truth: GroundTruthBundle,
    ) -> list[ValidationCheck]:
        checks: list[ValidationCheck] = []

        transactions_path = (
            self.synthetic_dir
            / "transactions.csv"
        )

        with transactions_path.open(
            "r",
            newline="",
            encoding="utf-8",
        ) as file:
            reader = csv.reader(file)
            header = next(reader)

        leakage_fields = {
            "scenario_id",
            "scenario_label",
            "ground_truth",
        }.intersection(header)

        checks.append(
            ValidationCheck(
                name="ground_truth_leakage",
                status=(
                    ValidationStatus.PASS
                    if not leakage_fields
                    else ValidationStatus.FAIL
                ),
                expected=(
                    "Public transaction data contains "
                    "no hidden scenario fields."
                ),
                observed=(
                    "No leakage detected"
                    if not leakage_fields
                    else ", ".join(
                        sorted(leakage_fields)
                    )
                ),
                message=(
                    "Scenario labels remain evaluator-only."
                    if not leakage_fields
                    else "Hidden labels leaked into public data."
                ),
            )
        )

        transaction_ids = [
            transaction.transaction_id
            for transaction
            in bundle.transactions
        ]

        unique_ids = (
            len(transaction_ids)
            == len(set(transaction_ids))
        )

        checks.append(
            ValidationCheck(
                name="unique_transaction_ids",
                status=(
                    ValidationStatus.PASS
                    if unique_ids
                    else ValidationStatus.FAIL
                ),
                expected=(
                    "Every public transaction ID is unique."
                ),
                observed=(
                    f"{len(set(transaction_ids))} unique "
                    f"out of {len(transaction_ids)}"
                ),
                message=(
                    "Transaction IDs are unique."
                    if unique_ids
                    else "Duplicate transaction IDs exist."
                ),
            )
        )

        sorted_transactions = sorted(
            bundle.transactions,
            key=lambda item: (
                item.timestamp,
                item.transaction_id,
            ),
        )

        chronological = (
            list(bundle.transactions)
            == sorted_transactions
        )

        checks.append(
            ValidationCheck(
                name="chronological_transactions",
                status=(
                    ValidationStatus.PASS
                    if chronological
                    else ValidationStatus.FAIL
                ),
                expected=(
                    "Public transactions are chronologically sorted."
                ),
                observed=(
                    "Chronological"
                    if chronological
                    else "Out of order"
                ),
                message=(
                    "Transaction stream ordering is valid."
                    if chronological
                    else "Transaction stream is not ordered."
                ),
            )
        )

        ground_truth_transaction_ids = {
            transaction_id
            for scenario
            in ground_truth.scenarios
            for transaction_id
            in scenario.transaction_ids
        }

        public_transaction_ids = set(
            transaction_ids
        )

        missing_references = (
            ground_truth_transaction_ids
            - public_transaction_ids
        )

        checks.append(
            ValidationCheck(
                name=(
                    "ground_truth_transaction_references"
                ),
                status=(
                    ValidationStatus.PASS
                    if not missing_references
                    else ValidationStatus.FAIL
                ),
                expected=(
                    "Every hidden transaction label references "
                    "a public transaction."
                ),
                observed=(
                    "All references valid"
                    if not missing_references
                    else (
                        f"{len(missing_references)} "
                        "missing references"
                    )
                ),
                message=(
                    "Ground-truth references are valid."
                    if not missing_references
                    else "Some hidden labels reference "
                    "unknown transactions."
                ),
            )
        )

        manifest_invariants = bool(
            ground_truth.manifest.get(
                "financial_invariants_passed",
                False,
            )
        )

        checks.append(
            ValidationCheck(
                name="financial_invariants",
                status=(
                    ValidationStatus.PASS
                    if manifest_invariants
                    else ValidationStatus.FAIL
                ),
                expected=(
                    "Simulator reports no unexplained "
                    "negative financial balances."
                ),
                observed=str(
                    manifest_invariants
                ),
                message=(
                    "Financial invariants passed."
                    if manifest_invariants
                    else "Financial invariant validation failed."
                ),
            )
        )

        return checks

    def _build_metrics(
        self,
        *,
        scenario_results: list[
            ScenarioValidationResult
        ],
        trackers: dict[
            str,
            ScenarioTracker,
        ],
        event_latencies_ms: list[float],
        fusion_engine: DecisionFusionEngine,
        case_service: CaseCoordinationService,
    ) -> list[ValidationMetric]:
        by_label = {
            result.label: result
            for result in scenario_results
        }

        anomaly_positive_labels = {
            "REPEATED_CASH_OUT_CLUSTER",
            "CROSS_PROVIDER_LINKED_ACTIVITY",
        }

        true_positives = sum(
            result.passed
            for result in scenario_results
            if result.label
            in anomaly_positive_labels
        )

        false_negatives = (
            len(anomaly_positive_labels)
            - true_positives
        )

        legitimate_tracker = next(
            (
                trackers[result.scenario_id]
                for result in scenario_results
                if result.label
                == "LEGITIMATE_DEMAND_SPIKE"
            ),
            ScenarioTracker(),
        )

        false_positives = int(
            legitimate_tracker.flags.get(
                "false_positive_observed",
                False,
            )
        )

        precision_denominator = (
            true_positives
            + false_positives
        )

        anomaly_precision = (
            true_positives
            / precision_denominator
            if precision_denominator
            else 1.0
        )

        recall_denominator = (
            true_positives
            + false_negatives
        )

        anomaly_recall = (
            true_positives
            / recall_denominator
            if recall_denominator
            else 1.0
        )

        hard_negative_fpr = float(
            false_positives
        )

        data_quality_labels = {
            "FEED_DELAY_RECOVERY",
            "BALANCE_CONFLICT",
        }

        data_quality_results = [
            result
            for result in scenario_results
            if result.label
            in data_quality_labels
        ]

        data_quality_coverage = (
            sum(
                result.passed
                for result
                in data_quality_results
            )
            / len(data_quality_results)
            if data_quality_results
            else 0
        )

        liquidity_result = by_label.get(
            "HIDDEN_PROVIDER_SHORTAGE"
        )

        liquidity_coverage = (
            1.0
            if (
                liquidity_result is not None
                and liquidity_result.passed
            )
            else 0.0
        )

        shortage_lead_time = 0.0

        if liquidity_result is not None:
            raw_value = (
                liquidity_result
                .observations
                .get(
                    "minutes_to_depletion"
                )
            )

            if isinstance(
                raw_value,
                (float, int),
            ):
                shortage_lead_time = float(
                    raw_value
                )

        incidents = (
            fusion_engine
            .get_all_incidents()
        )

        explained_incidents = [
            incident
            for incident in incidents
            if incident.evidence
            and incident.uncertainty
            and incident
            .recommended_next_step.strip()
        ]

        explanation_coverage = (
            len(explained_incidents)
            / len(incidents)
            if incidents
            else 1.0
        )

        cases = case_service.list_cases()

        case_incident_ids = {
            case.incident_id
            for case in cases
        }

        incident_case_coverage = (
            sum(
                incident.incident_id
                in case_incident_ids
                for incident in incidents
            )
            / len(incidents)
            if incidents
            else 1.0
        )

        average_latency = (
            mean(event_latencies_ms)
            if event_latencies_ms
            else 0
        )

        p95_latency = self._percentile(
            event_latencies_ms,
            95,
        )

        return [
            self._higher_is_better_metric(
                name="anomaly_precision",
                value=anomaly_precision,
                unit="ratio",
                minimum=0.80,
                description=(
                    "Share of anomaly review detections "
                    "that correspond to injected positive scenarios."
                ),
            ),
            self._higher_is_better_metric(
                name="anomaly_recall",
                value=anomaly_recall,
                unit="ratio",
                minimum=0.80,
                description=(
                    "Share of injected anomaly scenarios detected."
                ),
            ),
            self._lower_is_better_metric(
                name=(
                    "hard_negative_false_positive_rate"
                ),
                value=hard_negative_fpr,
                unit="ratio",
                maximum=0.20,
                description=(
                    "False-review rate for the legitimate "
                    "Eid-demand hard negative."
                ),
            ),
            self._higher_is_better_metric(
                name=(
                    "data_quality_detection_coverage"
                ),
                value=data_quality_coverage,
                unit="ratio",
                minimum=1.0,
                description=(
                    "Coverage of feed-delay/recovery and "
                    "balance-conflict scenarios."
                ),
            ),
            self._higher_is_better_metric(
                name=(
                    "liquidity_detection_coverage"
                ),
                value=liquidity_coverage,
                unit="ratio",
                minimum=1.0,
                description=(
                    "Detection coverage for hidden "
                    "provider shortage."
                ),
            ),
            ValidationMetric(
                name="shortage_lead_time",
                value=round(
                    shortage_lead_time,
                    3,
                ),
                unit="minutes",
                target="> 0 minutes",
                status=(
                    ValidationStatus.PASS
                    if shortage_lead_time > 0
                    else ValidationStatus.WARNING
                ),
                passed=(
                    shortage_lead_time > 0
                ),
                description=(
                    "Projected time remaining until provider "
                    "e-money depletion at first detected pressure."
                ),
            ),
            self._higher_is_better_metric(
                name=(
                    "incident_explanation_coverage"
                ),
                value=explanation_coverage,
                unit="ratio",
                minimum=0.95,
                description=(
                    "Incidents containing evidence, uncertainty "
                    "and a safe recommended next step."
                ),
            ),
            self._higher_is_better_metric(
                name="incident_case_coverage",
                value=incident_case_coverage,
                unit="ratio",
                minimum=0.95,
                description=(
                    "Share of generated incidents linked to "
                    "traceable coordination cases."
                ),
            ),
            ValidationMetric(
                name=(
                    "average_event_processing_latency"
                ),
                value=round(
                    average_latency,
                    3,
                ),
                unit="milliseconds",
                target="Informational",
                status=ValidationStatus.PASS,
                passed=True,
                description=(
                    "Average complete pipeline processing "
                    "time per replayed event."
                ),
            ),
            self._lower_is_better_metric(
                name=(
                    "p95_event_processing_latency"
                ),
                value=p95_latency,
                unit="milliseconds",
                maximum=250.0,
                description=(
                    "95th-percentile complete pipeline "
                    "processing time per replayed event."
                ),
            ),
        ]

    @staticmethod
    def _higher_is_better_metric(
        *,
        name: str,
        value: float,
        unit: str,
        minimum: float,
        description: str,
    ) -> ValidationMetric:
        passed = value >= minimum

        return ValidationMetric(
            name=name,
            value=round(value, 4),
            unit=unit,
            target=f">= {minimum}",
            status=(
                ValidationStatus.PASS
                if passed
                else ValidationStatus.FAIL
            ),
            passed=passed,
            description=description,
        )

    @staticmethod
    def _lower_is_better_metric(
        *,
        name: str,
        value: float,
        unit: str,
        maximum: float,
        description: str,
    ) -> ValidationMetric:
        passed = value <= maximum

        return ValidationMetric(
            name=name,
            value=round(value, 4),
            unit=unit,
            target=f"<= {maximum}",
            status=(
                ValidationStatus.PASS
                if passed
                else ValidationStatus.FAIL
            ),
            passed=passed,
            description=description,
        )

    @staticmethod
    def _percentile(
        values: list[float],
        percentile: float,
    ) -> float:
        if not values:
            return 0.0

        ordered = sorted(values)

        index = (
            percentile / 100
            * (len(ordered) - 1)
        )

        lower = math.floor(index)
        upper = math.ceil(index)

        if lower == upper:
            return round(
                ordered[lower],
                3,
            )

        weight = index - lower

        result = (
            ordered[lower]
            * (1 - weight)
            + ordered[upper]
            * weight
        )

        return round(result, 3)