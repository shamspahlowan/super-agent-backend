from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from statistics import median
from threading import RLock

from app.ingestion.canonical_event import (
    AgentRecord,
    ContextEvent,
    ProviderID,
    TransactionEvent,
    TransactionStatus,
)
from app.schemas.anomaly import (
    AnomalyAssessment,
    AnomalyBand,
    AnomalyCategory,
    AnomalyFactor,
    AnomalySummary,
)


ZERO = Decimal("0")


class AnomalyDetectionError(RuntimeError):
    """Base anomaly-engine exception."""


class UnknownAnomalyAgentError(AnomalyDetectionError):
    """Raised when the requested agent is unknown."""


class DuplicateAnomalyObservationError(
    AnomalyDetectionError
):
    """Raised when one transaction is recorded twice."""


class AnomalyDetectionEngine:
    """
    Explainable deterministic anomaly detector.

    It detects patterns requiring review but never makes a
    fraud determination.
    """

    def __init__(
        self,
        *,
        window_minutes: int = 15,
        baseline_minutes: int = 180,
        minimum_transactions: int = 6,
        amount_tolerance_percent: float = 2.0,
        medium_threshold: int = 40,
        high_threshold: int = 70,
    ) -> None:
        if window_minutes <= 0:
            raise AnomalyDetectionError(
                "window_minutes must be greater than zero."
            )

        if baseline_minutes <= window_minutes:
            raise AnomalyDetectionError(
                "baseline_minutes must exceed window_minutes."
            )

        if minimum_transactions <= 0:
            raise AnomalyDetectionError(
                "minimum_transactions must be positive."
            )

        if medium_threshold >= high_threshold:
            raise AnomalyDetectionError(
                "medium_threshold must be below high_threshold."
            )

        self.window_minutes = window_minutes
        self.baseline_minutes = baseline_minutes
        self.minimum_transactions = minimum_transactions

        self.amount_tolerance = Decimal(
            str(amount_tolerance_percent)
        ) / Decimal("100")

        self.medium_threshold = medium_threshold
        self.high_threshold = high_threshold

        self._agent_areas: dict[str, str] = {}
        self._context_events: list[ContextEvent] = []

        self._transactions: dict[
            str,
            list[TransactionEvent],
        ] = defaultdict(list)

        self._recorded_transaction_ids: set[str] = set()

        self._lock = RLock()

    def initialize(
        self,
        *,
        agents: list[AgentRecord],
        context_events: list[ContextEvent],
    ) -> None:
        with self._lock:
            self._agent_areas = {
                agent.agent_id: agent.area
                for agent in agents
            }

            self._context_events = list(context_events)

            self._transactions.clear()
            self._recorded_transaction_ids.clear()

        if not self._agent_areas:
            raise AnomalyDetectionError(
                "At least one agent is required."
            )

    def record_transaction(
        self,
        transaction: TransactionEvent,
    ) -> None:
        with self._lock:
            if transaction.agent_id not in self._agent_areas:
                raise UnknownAnomalyAgentError(
                    f"Unknown agent: {transaction.agent_id}"
                )

            if (
                transaction.transaction_id
                in self._recorded_transaction_ids
            ):
                raise DuplicateAnomalyObservationError(
                    "Transaction already recorded by anomaly engine: "
                    f"{transaction.transaction_id}"
                )

            self._recorded_transaction_ids.add(
                transaction.transaction_id
            )

            self._transactions[
                transaction.agent_id
            ].append(transaction)

    def get_agent_assessment(
        self,
        *,
        agent_id: str,
        as_of: datetime,
        provider_id: ProviderID | None = None,
    ) -> AnomalyAssessment:
        self._validate_timestamp(as_of)

        if agent_id not in self._agent_areas:
            raise UnknownAnomalyAgentError(
                f"Unknown agent: {agent_id}"
            )

        window_start = as_of - timedelta(
            minutes=self.window_minutes
        )

        baseline_start = window_start - timedelta(
            minutes=self.baseline_minutes
        )

        recent = self._select_transactions(
            agent_id=agent_id,
            start=window_start,
            end=as_of,
            provider_id=provider_id,
        )

        baseline = self._select_transactions(
            agent_id=agent_id,
            start=baseline_start,
            end=window_start,
            provider_id=provider_id,
            end_inclusive=False,
        )

        successful = [
            transaction
            for transaction in recent
            if transaction.status
            == TransactionStatus.SUCCESS
        ]

        failed = [
            transaction
            for transaction in recent
            if transaction.status
            == TransactionStatus.FAILED
        ]

        baseline_successful = [
            transaction
            for transaction in baseline
            if transaction.status
            == TransactionStatus.SUCCESS
        ]

        transaction_count = len(recent)
        successful_count = len(successful)
        failed_count = len(failed)

        total_successful_amount = sum(
            (
                transaction.amount
                for transaction in successful
            ),
            ZERO,
        )

        account_counts = Counter(
            transaction.account_id
            for transaction in successful
        )

        unique_accounts = len(account_counts)

        unique_providers = len(
            {
                transaction.provider_id
                for transaction in successful
            }
        )

        dominant_account_ratio = (
            max(account_counts.values()) / successful_count
            if successful_count
            else 0
        )

        (
            repeated_amount_ratio,
            repeated_transaction_ids,
            repeated_amount_value,
        ) = self._repeated_amount_cluster(successful)

        factors: list[AnomalyFactor] = []
        score = 0

        # 1. Transaction velocity
        recent_rate = (
            successful_count / self.window_minutes
        )

        baseline_rate = (
            len(baseline_successful)
            / self.baseline_minutes
        )

        velocity_ratio = (
            recent_rate / baseline_rate
            if baseline_rate > 0
            else None
        )

        velocity_triggered = (
            successful_count >= 8
            and (
                velocity_ratio is None
                or velocity_ratio >= 2
            )
        )

        if velocity_triggered:
            points = 30
            score += points

            factors.append(
                AnomalyFactor(
                    code="TRANSACTION_VELOCITY",
                    points=points,
                    description=(
                        "Transaction activity increased sharply "
                        "inside the review window."
                    ),
                    value=(
                        f"count={successful_count}, "
                        f"rate={recent_rate:.2f}/minute, "
                        f"baseline_ratio="
                        f"{velocity_ratio:.2f}"
                        if velocity_ratio is not None
                        else (
                            f"count={successful_count}, "
                            "baseline activity was insufficient"
                        )
                    ),
                    transaction_ids=[
                        transaction.transaction_id
                        for transaction in successful
                    ][:20],
                )
            )

        # 2. Near-identical amounts
        if (
            successful_count >= self.minimum_transactions
            and repeated_amount_ratio >= 0.65
        ):
            points = 25
            score += points

            factors.append(
                AnomalyFactor(
                    code="NEAR_IDENTICAL_AMOUNTS",
                    points=points,
                    description=(
                        "A large share of recent transactions "
                        "had near-identical amounts."
                    ),
                    value=(
                        f"ratio={repeated_amount_ratio:.2f}, "
                        f"representative_amount="
                        f"{repeated_amount_value}"
                    ),
                    transaction_ids=(
                        repeated_transaction_ids[:20]
                    ),
                )
            )

        # 3. Concentration among a small account group
        concentrated_accounts = (
            successful_count >= self.minimum_transactions
            and (
                unique_accounts <= 3
                or dominant_account_ratio >= 0.50
            )
        )

        if concentrated_accounts:
            points = 20
            score += points

            concentrated_ids = [
                transaction.transaction_id
                for transaction in successful
                if transaction.account_id
                in {
                    account_id
                    for account_id, _ in
                    account_counts.most_common(3)
                }
            ]

            factors.append(
                AnomalyFactor(
                    code="ACCOUNT_CONCENTRATION",
                    points=points,
                    description=(
                        "Recent activity was concentrated among "
                        "a small number of accounts."
                    ),
                    value=(
                        f"unique_accounts={unique_accounts}, "
                        f"dominant_ratio="
                        f"{dominant_account_ratio:.2f}"
                    ),
                    transaction_ids=concentrated_ids[:20],
                )
            )

        # 4. Cross-provider linked activity
        cross_provider_accounts = (
            self._cross_provider_accounts(successful)
        )

        cross_provider_transaction_ids = [
            transaction.transaction_id
            for transaction in successful
            if transaction.account_id
            in cross_provider_accounts
        ]

        if (
            provider_id is None
            and cross_provider_accounts
            and len(cross_provider_transaction_ids) >= 4
        ):
            points = 25
            score += points

            factors.append(
                AnomalyFactor(
                    code="CROSS_PROVIDER_LINK",
                    points=points,
                    description=(
                        "A synthetic linked identifier appeared "
                        "across multiple provider contexts."
                    ),
                    value=(
                        f"linked_accounts="
                        f"{len(cross_provider_accounts)}, "
                        f"providers={unique_providers}"
                    ),
                    transaction_ids=(
                        cross_provider_transaction_ids[:20]
                    ),
                )
            )

        # 5. Unusual amount compared with earlier baseline
        recent_median = self._median_amount(successful)
        baseline_median = self._median_amount(
            baseline_successful
        )

        unusual_amount = (
            recent_median is not None
            and baseline_median is not None
            and len(baseline_successful) >= 5
            and recent_median
            >= baseline_median * Decimal("2.5")
        )

        if unusual_amount:
            points = 15
            score += points

            factors.append(
                AnomalyFactor(
                    code="UNUSUAL_AMOUNT_LEVEL",
                    points=points,
                    description=(
                        "Recent transaction amounts were much "
                        "higher than the earlier baseline."
                    ),
                    value=(
                        f"recent_median={recent_median}, "
                        f"baseline_median={baseline_median}"
                    ),
                    transaction_ids=[
                        transaction.transaction_id
                        for transaction in successful
                    ][:20],
                )
            )

        # 6. Abnormal failed-transaction rate
        failure_rate = (
            failed_count / transaction_count
            if transaction_count
            else 0
        )

        if (
            transaction_count >= 6
            and failed_count >= 3
            and failure_rate >= 0.35
        ):
            points = 15
            score += points

            factors.append(
                AnomalyFactor(
                    code="ABNORMAL_FAILURE_RATE",
                    points=points,
                    description=(
                        "The recent failed-transaction rate "
                        "was unusually high."
                    ),
                    value=(
                        f"failed={failed_count}, "
                        f"failure_rate={failure_rate:.2f}"
                    ),
                    transaction_ids=[
                        transaction.transaction_id
                        for transaction in failed
                    ][:20],
                )
            )

        active_contexts = self._active_contexts(
            agent_id=agent_id,
            as_of=as_of,
        )

        broad_account_diversity = (
            successful_count >= 8
            and unique_accounts
            >= max(
                6,
                ceil(successful_count * 0.70),
            )
        )

        broad_provider_diversity = (
            unique_providers >= 2
        )

        context_adjustment_applied = (
            bool(active_contexts)
            and broad_account_diversity
            and broad_provider_diversity
            and repeated_amount_ratio < 0.50
            and dominant_account_ratio < 0.25
        )

        if context_adjustment_applied:
            reduction = min(score, 35)
            score -= reduction

            factors.append(
                AnomalyFactor(
                    code="CONTEXTUAL_DEMAND_ADJUSTMENT",
                    points=-reduction,
                    description=(
                        "The activity is broadly distributed "
                        "and overlaps with a known local demand event."
                    ),
                    value=", ".join(active_contexts),
                )
            )

        score = min(max(score, 0), 100)

        band = self._score_band(score)

        has_cross_provider_factor = any(
            factor.code == "CROSS_PROVIDER_LINK"
            for factor in factors
        )

        if (
            context_adjustment_applied
            and score < self.medium_threshold
        ):
            category = (
                AnomalyCategory.LEGITIMATE_DEMAND_SPIKE
            )

        elif (
            has_cross_provider_factor
            and score >= self.medium_threshold
        ):
            category = (
                AnomalyCategory.CROSS_PROVIDER_REVIEW
            )

        elif score >= self.medium_threshold:
            category = AnomalyCategory.REQUIRES_REVIEW

        else:
            category = AnomalyCategory.NORMAL_ACTIVITY

        requires_human_review = (
            score >= self.medium_threshold
        )

        confidence = self._calculate_confidence(
            successful_count=successful_count,
            baseline_count=len(baseline_successful),
        )

        evidence_transaction_ids = self._collect_evidence_ids(
            factors=factors,
            fallback_transactions=recent,
        )

        alternative_explanations = (
            self._alternative_explanations(
                active_contexts=active_contexts,
                failure_factor=any(
                    factor.code
                    == "ABNORMAL_FAILURE_RATE"
                    for factor in factors
                ),
            )
        )

        summary, safe_next_step = self._render_outcome(
            category=category,
            score=score,
        )

        provider_scope = sorted(
            {
                transaction.provider_id
                for transaction in recent
            },
            key=lambda provider: provider.value,
        )

        if provider_id is not None and not provider_scope:
            provider_scope = [provider_id]

        return AnomalyAssessment(
            agent_id=agent_id,
            provider_scope=provider_scope,
            as_of=as_of,
            window_start=window_start,
            score=score,
            band=band,
            category=category,
            requires_human_review=requires_human_review,
            transaction_count=transaction_count,
            successful_transactions=successful_count,
            failed_transactions=failed_count,
            total_successful_amount=total_successful_amount,
            unique_accounts=unique_accounts,
            unique_providers=unique_providers,
            repeated_amount_ratio=round(
                repeated_amount_ratio,
                3,
            ),
            dominant_account_ratio=round(
                dominant_account_ratio,
                3,
            ),
            confidence=confidence,
            active_contexts=active_contexts,
            factors=factors,
            evidence_transaction_ids=(
                evidence_transaction_ids
            ),
            alternative_explanations=(
                alternative_explanations
            ),
            summary=summary,
            safe_next_step=safe_next_step,
        )

    def get_all_assessments(
        self,
        *,
        as_of: datetime,
    ) -> list[AnomalyAssessment]:
        return [
            self.get_agent_assessment(
                agent_id=agent_id,
                as_of=as_of,
            )
            for agent_id in sorted(self._agent_areas)
        ]

    def get_summary(
        self,
        *,
        as_of: datetime,
    ) -> AnomalySummary:
        assessments = self.get_all_assessments(
            as_of=as_of
        )

        return AnomalySummary(
            as_of=as_of,
            total_agents=len(assessments),
            low=sum(
                item.band == AnomalyBand.LOW
                for item in assessments
            ),
            medium=sum(
                item.band == AnomalyBand.MEDIUM
                for item in assessments
            ),
            high=sum(
                item.band == AnomalyBand.HIGH
                for item in assessments
            ),
            requires_review=sum(
                item.requires_human_review
                for item in assessments
            ),
            legitimate_demand_spikes=sum(
                item.category
                == AnomalyCategory.LEGITIMATE_DEMAND_SPIKE
                for item in assessments
            ),
            cross_provider_reviews=sum(
                item.category
                == AnomalyCategory.CROSS_PROVIDER_REVIEW
                for item in assessments
            ),
            assessments=assessments,
        )

    def _select_transactions(
        self,
        *,
        agent_id: str,
        start: datetime,
        end: datetime,
        provider_id: ProviderID | None,
        end_inclusive: bool = True,
    ) -> list[TransactionEvent]:
        with self._lock:
            transactions = self._transactions.get(
                agent_id,
                [],
            )

            return [
                transaction
                for transaction in transactions
                if transaction.timestamp >= start
                and (
                    transaction.timestamp <= end
                    if end_inclusive
                    else transaction.timestamp < end
                )
                and (
                    provider_id is None
                    or transaction.provider_id
                    == provider_id
                )
            ]

    def _repeated_amount_cluster(
        self,
        transactions: list[TransactionEvent],
    ) -> tuple[float, list[str], Decimal | None]:
        if not transactions:
            return 0, [], None

        best_cluster: list[TransactionEvent] = []

        for anchor in transactions:
            denominator = max(
                abs(anchor.amount),
                Decimal("1"),
            )

            cluster = [
                transaction
                for transaction in transactions
                if (
                    abs(
                        transaction.amount
                        - anchor.amount
                    )
                    / denominator
                )
                <= self.amount_tolerance
            ]

            if len(cluster) > len(best_cluster):
                best_cluster = cluster

        ratio = len(best_cluster) / len(transactions)

        representative_amount = (
            self._median_amount(best_cluster)
        )

        return (
            ratio,
            [
                transaction.transaction_id
                for transaction in best_cluster
            ],
            representative_amount,
        )

    @staticmethod
    def _cross_provider_accounts(
        transactions: list[TransactionEvent],
    ) -> set[str]:
        providers_by_account: dict[
            str,
            set[ProviderID],
        ] = defaultdict(set)

        for transaction in transactions:
            providers_by_account[
                transaction.account_id
            ].add(transaction.provider_id)

        return {
            account_id
            for account_id, providers
            in providers_by_account.items()
            if len(providers) >= 2
        }

    @staticmethod
    def _median_amount(
        transactions: list[TransactionEvent],
    ) -> Decimal | None:
        if not transactions:
            return None

        values = [
            transaction.amount
            for transaction in transactions
        ]

        return Decimal(str(median(values)))

    def _active_contexts(
        self,
        *,
        agent_id: str,
        as_of: datetime,
    ) -> list[str]:
        area = self._agent_areas[agent_id]

        return [
            context.event_type
            for context in self._context_events
            if context.area == area
            and context.start_time <= as_of <= context.end_time
        ]

    def _score_band(
        self,
        score: int,
    ) -> AnomalyBand:
        if score >= self.high_threshold:
            return AnomalyBand.HIGH

        if score >= self.medium_threshold:
            return AnomalyBand.MEDIUM

        return AnomalyBand.LOW

    def _calculate_confidence(
        self,
        *,
        successful_count: int,
        baseline_count: int,
    ) -> float:
        sample_factor = min(
            successful_count / 10,
            1,
        )

        baseline_factor = min(
            baseline_count / 20,
            1,
        )

        confidence = (
            0.45
            + 0.35 * sample_factor
            + 0.20 * baseline_factor
        )

        return round(
            min(max(confidence, 0), 0.98),
            3,
        )

    @staticmethod
    def _collect_evidence_ids(
        *,
        factors: list[AnomalyFactor],
        fallback_transactions: list[TransactionEvent],
    ) -> list[str]:
        evidence: list[str] = []

        for factor in factors:
            if factor.points <= 0:
                continue

            for transaction_id in factor.transaction_ids:
                if transaction_id not in evidence:
                    evidence.append(transaction_id)

        if not evidence:
            evidence = [
                transaction.transaction_id
                for transaction in fallback_transactions
            ]

        return evidence[:25]

    @staticmethod
    def _alternative_explanations(
        *,
        active_contexts: list[str],
        failure_factor: bool,
    ) -> list[str]:
        explanations = [
            "A legitimate salary-day or festival-related demand increase.",
            "A temporary change in local customer behaviour.",
        ]

        if active_contexts:
            explanations.insert(
                0,
                "A known local demand event is currently active: "
                + ", ".join(active_contexts),
            )

        if failure_factor:
            explanations.append(
                "A provider-side or connectivity problem may "
                "have increased failed transactions."
            )

        return explanations

    @staticmethod
    def _render_outcome(
        *,
        category: AnomalyCategory,
        score: int,
    ) -> tuple[str, str]:
        if category == AnomalyCategory.NORMAL_ACTIVITY:
            return (
                "No important unusual pattern was identified "
                "in the current review window.",
                "Continue normal monitoring.",
            )

        if (
            category
            == AnomalyCategory.LEGITIMATE_DEMAND_SPIKE
        ):
            return (
                "Activity increased, but it is broadly distributed "
                "and consistent with a known demand event.",
                "Continue monitoring and verify local demand "
                "before escalating.",
            )

        if (
            category
            == AnomalyCategory.CROSS_PROVIDER_REVIEW
        ):
            return (
                "Related unusual activity was observed across "
                "multiple provider contexts.",
                "Route a privacy-safe evidence summary for human "
                "review. Do not expose another provider's raw data.",
            )

        return (
            f"Unusual transaction behaviour requires review. "
            f"Current explainable score: {score}.",
            "Review the listed evidence and operational context. "
            "Do not block accounts or declare fraud automatically.",
        )

    @staticmethod
    def _validate_timestamp(
        value: datetime,
    ) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise AnomalyDetectionError(
                "Assessment timestamp must include timezone."
            )