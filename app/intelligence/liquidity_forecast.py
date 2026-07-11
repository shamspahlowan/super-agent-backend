from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from threading import RLock

from app.data_quality.trust_score import (
    FeedHealthEngine,
    UnknownFeedError,
)
from app.ingestion.canonical_event import (
    OpeningBalance,
    ProviderID,
    ResourceType,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)
from app.ledger.balance_engine import (
    BalanceEngine,
    UnknownAgentError,
    UnknownProviderBalanceError,
)
from app.schemas.data_quality import (
    FeedHealthStatus,
    FeedHealthView,
)
from app.schemas.liquidity import (
    AgentLiquidityForecast,
    LiquidityEvidence,
    LiquidityResource,
    LiquidityStatus,
    LiquiditySummary,
    ResourceLiquidityForecast,
)


ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


class LiquidityForecastError(RuntimeError):
    """Base exception for liquidity forecasting."""


class UnknownLiquidityResourceError(LiquidityForecastError):
    """Raised when an agent or provider cannot be forecast."""


class DuplicateLiquidityObservationError(LiquidityForecastError):
    """Raised when one transaction is recorded twice."""


class LiquidityForecastEngine:
    def __init__(
        self,
        *,
        balance_engine: BalanceEngine,
        feed_health_engine: FeedHealthEngine,
        lookback_minutes: int = 30,
        safety_buffer_percent: float = 15,
        watch_minutes: int = 120,
        critical_minutes: int = 60,
        min_successful_transactions: int = 3,
    ) -> None:
        if lookback_minutes <= 0:
            raise LiquidityForecastError(
                "lookback_minutes must be greater than zero."
            )

        if critical_minutes >= watch_minutes:
            raise LiquidityForecastError(
                "critical_minutes must be lower than watch_minutes."
            )

        if not 0 <= safety_buffer_percent <= 100:
            raise LiquidityForecastError(
                "safety_buffer_percent must be between 0 and 100."
            )

        self.balance_engine = balance_engine
        self.feed_health_engine = feed_health_engine

        self.lookback_minutes = lookback_minutes
        self.safety_buffer_percent = Decimal(
            str(safety_buffer_percent)
        )

        self.watch_minutes = watch_minutes
        self.critical_minutes = critical_minutes

        self.min_successful_transactions = (
            min_successful_transactions
        )

        self._opening_shared_cash: dict[str, Decimal] = {}

        self._opening_provider_emoney: dict[
            tuple[str, ProviderID],
            Decimal,
        ] = {}

        self._transactions: dict[
            str,
            list[TransactionEvent],
        ] = defaultdict(list)

        self._recorded_transaction_ids: set[str] = set()

        self._lock = RLock()

    def initialize(
        self,
        opening_balances: list[OpeningBalance],
    ) -> None:
        with self._lock:
            self._opening_shared_cash.clear()
            self._opening_provider_emoney.clear()
            self._transactions.clear()
            self._recorded_transaction_ids.clear()

            for balance in opening_balances:
                if balance.resource_type == ResourceType.SHARED_CASH:
                    self._opening_shared_cash[
                        balance.agent_id
                    ] = balance.opening_balance
                    continue

                if balance.provider_id is None:
                    continue

                self._opening_provider_emoney[
                    (
                        balance.agent_id,
                        balance.provider_id,
                    )
                ] = balance.opening_balance

        if not self._opening_shared_cash:
            raise LiquidityForecastError(
                "No shared-cash opening balances were provided."
            )

        if not self._opening_provider_emoney:
            raise LiquidityForecastError(
                "No provider e-money opening balances were provided."
            )

    def record_transaction(
        self,
        transaction: TransactionEvent,
    ) -> None:
        with self._lock:
            if (
                transaction.transaction_id
                in self._recorded_transaction_ids
            ):
                raise DuplicateLiquidityObservationError(
                    "Transaction already recorded by liquidity engine: "
                    f"{transaction.transaction_id}"
                )

            self._recorded_transaction_ids.add(
                transaction.transaction_id
            )

            if transaction.status != TransactionStatus.SUCCESS:
                return

            self._transactions[
                transaction.agent_id
            ].append(transaction)

    def get_shared_cash_forecast(
        self,
        *,
        agent_id: str,
        as_of: datetime,
    ) -> ResourceLiquidityForecast:
        self._validate_timestamp(as_of)

        try:
            balance = self.balance_engine.get_agent_balance(
                agent_id
            )
            feed_health = self.feed_health_engine.get_agent_health(
                agent_id=agent_id,
                as_of=as_of,
            )
        except (UnknownAgentError, UnknownFeedError) as exc:
            raise UnknownLiquidityResourceError(
                str(exc)
            ) from exc

        opening_balance = self._opening_shared_cash.get(agent_id)

        if opening_balance is None:
            raise UnknownLiquidityResourceError(
                f"No opening shared-cash balance for {agent_id}."
            )

        recent = self._recent_transactions(
            agent_id=agent_id,
            as_of=as_of,
        )

        cash_in_total = sum(
            (
                transaction.amount
                for transaction in recent
                if transaction.transaction_type
                == TransactionType.CASH_IN
            ),
            ZERO,
        )

        cash_out_total = sum(
            (
                transaction.amount
                for transaction in recent
                if transaction.transaction_type
                == TransactionType.CASH_OUT
            ),
            ZERO,
        )

        net_depletion = cash_out_total - cash_in_total

        weakest_feed = self._weakest_feed(feed_health)

        driver_provider = self._shared_cash_driver(recent)

        return self._build_forecast(
            agent_id=agent_id,
            provider_id=None,
            resource=LiquidityResource.SHARED_CASH,
            current_balance=balance.shared_cash,
            opening_balance=opening_balance,
            recent_transactions=recent,
            net_depletion=net_depletion,
            as_of=as_of,
            feed_status=weakest_feed.status,
            feed_confidence=min(
                item.confidence for item in feed_health
            ),
            strong_recommendation_allowed=all(
                item.can_issue_strong_recommendation
                for item in feed_health
            ),
            driver_provider_id=driver_provider,
            cash_in_total=cash_in_total,
            cash_out_total=cash_out_total,
        )

    def get_provider_forecast(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID,
        as_of: datetime,
    ) -> ResourceLiquidityForecast:
        self._validate_timestamp(as_of)

        key = (
            agent_id,
            provider_id,
        )

        opening_balance = self._opening_provider_emoney.get(key)

        if opening_balance is None:
            raise UnknownLiquidityResourceError(
                "No opening provider balance for "
                f"agent={agent_id}, provider={provider_id.value}."
            )

        try:
            current_balance = (
                self.balance_engine.get_provider_balance(
                    agent_id,
                    provider_id,
                )
            )

            feed_health = (
                self.feed_health_engine.get_feed_health(
                    agent_id=agent_id,
                    provider_id=provider_id,
                    as_of=as_of,
                )
            )

        except (
            UnknownProviderBalanceError,
            UnknownFeedError,
        ) as exc:
            raise UnknownLiquidityResourceError(
                str(exc)
            ) from exc

        recent = [
            transaction
            for transaction in self._recent_transactions(
                agent_id=agent_id,
                as_of=as_of,
            )
            if transaction.provider_id == provider_id
        ]

        cash_in_total = sum(
            (
                transaction.amount
                for transaction in recent
                if transaction.transaction_type
                == TransactionType.CASH_IN
            ),
            ZERO,
        )

        cash_out_total = sum(
            (
                transaction.amount
                for transaction in recent
                if transaction.transaction_type
                == TransactionType.CASH_OUT
            ),
            ZERO,
        )

        # Cash-in consumes provider e-money.
        # Cash-out adds provider e-money.
        net_depletion = cash_in_total - cash_out_total

        return self._build_forecast(
            agent_id=agent_id,
            provider_id=provider_id,
            resource=LiquidityResource.PROVIDER_EMONEY,
            current_balance=current_balance,
            opening_balance=opening_balance,
            recent_transactions=recent,
            net_depletion=net_depletion,
            as_of=as_of,
            feed_status=feed_health.status,
            feed_confidence=feed_health.confidence,
            strong_recommendation_allowed=(
                feed_health.can_issue_strong_recommendation
            ),
            driver_provider_id=provider_id,
            cash_in_total=cash_in_total,
            cash_out_total=cash_out_total,
        )

    def get_agent_forecast(
        self,
        *,
        agent_id: str,
        as_of: datetime,
    ) -> AgentLiquidityForecast:
        shared_cash = self.get_shared_cash_forecast(
            agent_id=agent_id,
            as_of=as_of,
        )

        try:
            agent_balance = self.balance_engine.get_agent_balance(
                agent_id
            )
        except UnknownAgentError as exc:
            raise UnknownLiquidityResourceError(
                str(exc)
            ) from exc

        provider_forecasts = [
            self.get_provider_forecast(
                agent_id=agent_id,
                provider_id=provider.provider_id,
                as_of=as_of,
            )
            for provider in agent_balance.provider_balances
        ]

        provider_forecasts.sort(
            key=lambda forecast: forecast.provider_id.value
            if forecast.provider_id
            else ""
        )

        all_forecasts = [
            shared_cash,
            *provider_forecasts,
        ]

        most_urgent = max(
            all_forecasts,
            key=self._urgency_sort_key,
        )

        overall_status = most_urgent.status

        aggregate_confidence = min(
            forecast.confidence
            for forecast in all_forecasts
        )

        risky_provider_statuses = {
            LiquidityStatus.WATCH,
            LiquidityStatus.CRITICAL,
            LiquidityStatus.DEPLETED,
        }

        hidden_provider_shortage = (
            shared_cash.status == LiquidityStatus.SAFE
            and any(
                forecast.status in risky_provider_statuses
                for forecast in provider_forecasts
            )
        )

        warnings: list[str] = []

        if any(
            forecast.status
            == LiquidityStatus.INSUFFICIENT_DATA
            for forecast in all_forecasts
        ):
            warnings.append(
                "One or more forecasts have insufficient or "
                "unreliable provider data."
            )

        if hidden_provider_shortage:
            headline = (
                "The combined cash position appears healthy, "
                "but one provider balance is under pressure."
            )

        elif overall_status == LiquidityStatus.DEPLETED:
            headline = (
                "At least one operational liquidity resource "
                "is already depleted."
            )

        elif overall_status == LiquidityStatus.CRITICAL:
            headline = (
                "Critical liquidity pressure requires prompt "
                "human coordination."
            )

        elif overall_status == LiquidityStatus.WATCH:
            headline = (
                "Liquidity pressure is developing and should "
                "be monitored."
            )

        elif overall_status == LiquidityStatus.INSUFFICIENT_DATA:
            headline = (
                "Reliable liquidity forecasting is not currently "
                "possible."
            )

        else:
            headline = (
                "No immediate liquidity shortage is projected."
            )

        return AgentLiquidityForecast(
            agent_id=agent_id,
            as_of=as_of,
            overall_status=overall_status,
            aggregate_confidence=round(
                aggregate_confidence,
                3,
            ),
            shared_cash=shared_cash,
            provider_forecasts=provider_forecasts,
            hidden_provider_shortage=(
                hidden_provider_shortage
            ),
            most_urgent_resource=most_urgent.resource,
            most_urgent_provider=most_urgent.provider_id,
            headline=headline,
            warnings=warnings,
        )

    def get_all_agent_forecasts(
        self,
        *,
        as_of: datetime,
    ) -> list[AgentLiquidityForecast]:
        balances = self.balance_engine.get_all_agent_balances()

        return [
            self.get_agent_forecast(
                agent_id=balance.agent_id,
                as_of=as_of,
            )
            for balance in balances
        ]

    def get_summary(
        self,
        *,
        as_of: datetime,
    ) -> LiquiditySummary:
        agents = self.get_all_agent_forecasts(
            as_of=as_of
        )

        return LiquiditySummary(
            as_of=as_of,
            total_agents=len(agents),
            safe=sum(
                item.overall_status == LiquidityStatus.SAFE
                for item in agents
            ),
            watch=sum(
                item.overall_status == LiquidityStatus.WATCH
                for item in agents
            ),
            critical=sum(
                item.overall_status == LiquidityStatus.CRITICAL
                for item in agents
            ),
            depleted=sum(
                item.overall_status == LiquidityStatus.DEPLETED
                for item in agents
            ),
            insufficient_data=sum(
                item.overall_status
                == LiquidityStatus.INSUFFICIENT_DATA
                for item in agents
            ),
            hidden_provider_shortages=sum(
                item.hidden_provider_shortage
                for item in agents
            ),
            agents=agents,
        )

    def _build_forecast(
        self,
        *,
        agent_id: str,
        provider_id: ProviderID | None,
        resource: LiquidityResource,
        current_balance: Decimal,
        opening_balance: Decimal,
        recent_transactions: list[TransactionEvent],
        net_depletion: Decimal,
        as_of: datetime,
        feed_status: FeedHealthStatus,
        feed_confidence: float,
        strong_recommendation_allowed: bool,
        driver_provider_id: ProviderID | None,
        cash_in_total: Decimal,
        cash_out_total: Decimal,
    ) -> ResourceLiquidityForecast:
        reserve = (
            opening_balance
            * self.safety_buffer_percent
            / ONE_HUNDRED
        )

        observation_minutes = self._observation_minutes(
            recent_transactions,
            as_of,
        )

        transaction_count = len(recent_transactions)

        evidence = [
            LiquidityEvidence(
                code="CURRENT_BALANCE",
                message="Current operational balance.",
                value=str(current_balance),
            ),
            LiquidityEvidence(
                code="SAFETY_RESERVE",
                message="Configured safety reserve.",
                value=str(reserve),
            ),
            LiquidityEvidence(
                code="RECENT_ACTIVITY",
                message=(
                    "Successful transactions in the rolling window."
                ),
                value=str(transaction_count),
            ),
            LiquidityEvidence(
                code="CASH_FLOW",
                message=(
                    "Recent cash-in and cash-out totals."
                ),
                value=(
                    f"cash_in={cash_in_total}, "
                    f"cash_out={cash_out_total}"
                ),
            ),
            LiquidityEvidence(
                code="DATA_TRUST",
                message="Current provider-data trust status.",
                value=feed_status.value,
            ),
        ]

        severe_data_problem = feed_status in {
            FeedHealthStatus.MISSING,
            FeedHealthStatus.CONFLICTING,
        }

        if severe_data_problem:
            return ResourceLiquidityForecast(
                agent_id=agent_id,
                provider_id=provider_id,
                resource=resource,
                status=LiquidityStatus.INSUFFICIENT_DATA,
                current_balance=current_balance,
                opening_balance=opening_balance,
                safety_reserve=reserve,
                forecast_available=False,
                observed_window_minutes=(
                    observation_minutes
                ),
                successful_transactions=transaction_count,
                confidence=round(
                    min(feed_confidence, 0.35),
                    3,
                ),
                data_trust_status=feed_status,
                can_issue_strong_recommendation=False,
                driver_provider_id=driver_provider_id,
                evidence=evidence,
                recommendation=(
                    "Verify or restore the affected provider feed "
                    "before issuing a strong liquidity recommendation."
                ),
            )

        if current_balance <= ZERO:
            return ResourceLiquidityForecast(
                agent_id=agent_id,
                provider_id=provider_id,
                resource=resource,
                status=LiquidityStatus.DEPLETED,
                current_balance=current_balance,
                opening_balance=opening_balance,
                safety_reserve=reserve,
                forecast_available=True,
                net_depletion_per_minute=ZERO,
                observed_window_minutes=(
                    observation_minutes
                ),
                successful_transactions=transaction_count,
                minutes_to_safety_threshold=0,
                minutes_to_depletion=0,
                safety_threshold_at=as_of,
                projected_depletion_at=as_of,
                confidence=round(feed_confidence, 3),
                data_trust_status=feed_status,
                can_issue_strong_recommendation=(
                    strong_recommendation_allowed
                ),
                driver_provider_id=driver_provider_id,
                evidence=evidence,
                recommendation=(
                    "Escalate to the responsible operations team "
                    "and coordinate only through approved channels."
                ),
            )

        if current_balance <= reserve:
            return ResourceLiquidityForecast(
                agent_id=agent_id,
                provider_id=provider_id,
                resource=resource,
                status=LiquidityStatus.CRITICAL,
                current_balance=current_balance,
                opening_balance=opening_balance,
                safety_reserve=reserve,
                forecast_available=False,
                observed_window_minutes=(
                    observation_minutes
                ),
                successful_transactions=transaction_count,
                minutes_to_safety_threshold=0,
                safety_threshold_at=as_of,
                confidence=round(
                    feed_confidence * 0.8,
                    3,
                ),
                data_trust_status=feed_status,
                can_issue_strong_recommendation=(
                    strong_recommendation_allowed
                ),
                driver_provider_id=driver_provider_id,
                evidence=evidence,
                recommendation=(
                    "Contact the agent and begin approved liquidity "
                    "support coordination. No automatic transfer "
                    "should be performed."
                ),
            )

        if (
            transaction_count
            < self.min_successful_transactions
        ):
            return ResourceLiquidityForecast(
                agent_id=agent_id,
                provider_id=provider_id,
                resource=resource,
                status=LiquidityStatus.INSUFFICIENT_DATA,
                current_balance=current_balance,
                opening_balance=opening_balance,
                safety_reserve=reserve,
                forecast_available=False,
                observed_window_minutes=(
                    observation_minutes
                ),
                successful_transactions=transaction_count,
                confidence=round(
                    feed_confidence
                    * transaction_count
                    / self.min_successful_transactions,
                    3,
                ),
                data_trust_status=feed_status,
                can_issue_strong_recommendation=False,
                driver_provider_id=driver_provider_id,
                evidence=evidence,
                recommendation=(
                    "Continue monitoring until enough recent "
                    "transaction activity is available."
                ),
            )

        if observation_minutes <= 0:
            observation_minutes = 1

        depletion_rate = (
            max(net_depletion, ZERO)
            / Decimal(str(observation_minutes))
        )

        confidence = self._forecast_confidence(
            feed_confidence=feed_confidence,
            transaction_count=transaction_count,
            observation_minutes=observation_minutes,
        )

        evidence.append(
            LiquidityEvidence(
                code="NET_DEPLETION_RATE",
                message=(
                    "Estimated net depletion per simulated minute."
                ),
                value=str(
                    depletion_rate.quantize(
                        Decimal("0.01")
                    )
                ),
            )
        )

        if depletion_rate <= ZERO:
            return ResourceLiquidityForecast(
                agent_id=agent_id,
                provider_id=provider_id,
                resource=resource,
                status=LiquidityStatus.SAFE,
                current_balance=current_balance,
                opening_balance=opening_balance,
                safety_reserve=reserve,
                forecast_available=True,
                net_depletion_per_minute=ZERO,
                observed_window_minutes=(
                    observation_minutes
                ),
                successful_transactions=transaction_count,
                confidence=confidence,
                data_trust_status=feed_status,
                can_issue_strong_recommendation=(
                    strong_recommendation_allowed
                ),
                driver_provider_id=driver_provider_id,
                evidence=evidence,
                recommendation=(
                    "Continue monitoring. Current recent flow does "
                    "not indicate net depletion."
                ),
            )

        minutes_to_threshold = float(
            max(
                current_balance - reserve,
                ZERO,
            )
            / depletion_rate
        )

        minutes_to_depletion = float(
            current_balance / depletion_rate
        )

        threshold_at = as_of + timedelta(
            minutes=minutes_to_threshold
        )

        depletion_at = as_of + timedelta(
            minutes=minutes_to_depletion
        )

        if minutes_to_threshold <= self.critical_minutes:
            status = LiquidityStatus.CRITICAL
            recommendation = (
                "Contact the agent promptly and coordinate approved "
                "liquidity support. Review evidence before major action."
            )

        elif minutes_to_threshold <= self.watch_minutes:
            status = LiquidityStatus.WATCH
            recommendation = (
                "Notify the responsible operations role and prepare "
                "approved support before the safety threshold is reached."
            )

        else:
            status = LiquidityStatus.SAFE
            recommendation = (
                "No immediate shortage is projected. Continue monitoring."
            )

        return ResourceLiquidityForecast(
            agent_id=agent_id,
            provider_id=provider_id,
            resource=resource,
            status=status,
            current_balance=current_balance,
            opening_balance=opening_balance,
            safety_reserve=reserve,
            forecast_available=True,
            net_depletion_per_minute=(
                depletion_rate.quantize(
                    Decimal("0.01")
                )
            ),
            observed_window_minutes=round(
                observation_minutes,
                2,
            ),
            successful_transactions=transaction_count,
            minutes_to_safety_threshold=round(
                minutes_to_threshold,
                2,
            ),
            minutes_to_depletion=round(
                minutes_to_depletion,
                2,
            ),
            safety_threshold_at=threshold_at,
            projected_depletion_at=depletion_at,
            confidence=confidence,
            data_trust_status=feed_status,
            can_issue_strong_recommendation=(
                strong_recommendation_allowed
            ),
            driver_provider_id=driver_provider_id,
            evidence=evidence,
            recommendation=recommendation,
        )

    def _recent_transactions(
        self,
        *,
        agent_id: str,
        as_of: datetime,
    ) -> list[TransactionEvent]:
        window_start = as_of - timedelta(
            minutes=self.lookback_minutes
        )

        with self._lock:
            return [
                transaction
                for transaction in self._transactions.get(
                    agent_id,
                    [],
                )
                if window_start
                <= transaction.timestamp
                <= as_of
            ]

    def _observation_minutes(
        self,
        transactions: list[TransactionEvent],
        as_of: datetime,
    ) -> float:
        if not transactions:
            return 0

        earliest = min(
            transaction.timestamp
            for transaction in transactions
        )

        elapsed = (
            as_of - earliest
        ).total_seconds() / 60

        return round(
            max(
                1.0,
                min(
                    float(self.lookback_minutes),
                    elapsed,
                ),
            ),
            2,
        )

    def _forecast_confidence(
        self,
        *,
        feed_confidence: float,
        transaction_count: int,
        observation_minutes: float,
    ) -> float:
        sample_ratio = min(
            transaction_count / 10,
            1,
        )

        coverage_ratio = min(
            observation_minutes
            / self.lookback_minutes,
            1,
        )

        sample_factor = 0.55 + (
            0.45 * sample_ratio
        )

        coverage_factor = 0.75 + (
            0.25 * coverage_ratio
        )

        confidence = (
            feed_confidence
            * sample_factor
            * coverage_factor
        )

        return round(
            min(max(confidence, 0), 1),
            3,
        )

    @staticmethod
    def _weakest_feed(
        feeds: list[FeedHealthView],
    ) -> FeedHealthView:
        severity = {
            FeedHealthStatus.HEALTHY: 1,
            FeedHealthStatus.STALE: 2,
            FeedHealthStatus.MISSING: 3,
            FeedHealthStatus.CONFLICTING: 4,
        }

        return max(
            feeds,
            key=lambda feed: severity[feed.status],
        )

    @staticmethod
    def _shared_cash_driver(
        transactions: list[TransactionEvent],
    ) -> ProviderID | None:
        provider_pressure: dict[
            ProviderID,
            Decimal,
        ] = defaultdict(lambda: ZERO)

        for transaction in transactions:
            if transaction.transaction_type == TransactionType.CASH_OUT:
                provider_pressure[
                    transaction.provider_id
                ] += transaction.amount

            elif transaction.transaction_type == TransactionType.CASH_IN:
                provider_pressure[
                    transaction.provider_id
                ] -= transaction.amount

        if not provider_pressure:
            return None

        provider, pressure = max(
            provider_pressure.items(),
            key=lambda item: item[1],
        )

        return provider if pressure > ZERO else None

    @staticmethod
    def _urgency_sort_key(
        forecast: ResourceLiquidityForecast,
    ) -> tuple[int, float]:
        severity = {
            LiquidityStatus.SAFE: 1,
            LiquidityStatus.INSUFFICIENT_DATA: 2,
            LiquidityStatus.WATCH: 3,
            LiquidityStatus.CRITICAL: 4,
            LiquidityStatus.DEPLETED: 5,
        }

        eta = (
            forecast.minutes_to_safety_threshold
            if forecast.minutes_to_safety_threshold
            is not None
            else float("inf")
        )

        return (
            severity[forecast.status],
            -eta,
        )

    @staticmethod
    def _validate_timestamp(
        value: datetime,
    ) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise LiquidityForecastError(
                "Forecast timestamp must include timezone."
            )