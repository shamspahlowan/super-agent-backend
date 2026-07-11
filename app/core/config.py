from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class Settings(BaseSettings):
    # Application
    app_name: str = "Super Agent Liquidity & Risk Intelligence API"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"


    openai_api_key: SecretStr | None = None

    openai_explanations_enabled: bool = True

    openai_explanation_model: str = (
        "gpt-5.6-terra"
    )

    openai_explanation_timeout_seconds: float = Field(
        default=20,
        gt=0,
        le=120,
    )

    openai_explanation_max_output_tokens: int = Field(
        default=900,
        ge=200,
        le=3000,
    )

    # Database
    database_url: str = "sqlite:///./super_agent.db"
    sql_echo: bool = False

    # Frontend
    cors_origins: str = "http://localhost:3000"

    # Synthetic data and evaluation
    transactions_file: Path = Path("data/synthetic/transactions.csv")
    opening_balances_file: Path = Path(
        "data/synthetic/opening_balances.csv"
    )
    ground_truth_file: Path = Path(
        "data/ground_truth/scenario_labels.csv"
    )

    # Replay
    default_replay_speed: float = Field(default=10.0, gt=0)
    replay_batch_size: int = Field(default=20, gt=0)

    # Liquidity forecasting
    liquidity_lookback_minutes: int = Field(default=30, gt=0)
    liquidity_safety_buffer_percent: float = Field(
        default=15.0,
        ge=0,
        le=100,
    )
    liquidity_watch_minutes: int = Field(default=120, gt=0)
    liquidity_critical_minutes: int = Field(default=60, gt=0)

    # Data-quality thresholds
    feed_stale_minutes: int = Field(default=15, gt=0)
    feed_missing_minutes: int = Field(default=30, gt=0)

    # Anomaly score thresholds
    anomaly_medium_threshold: int = Field(default=40, ge=0, le=100)
    anomaly_high_threshold: int = Field(default=70, ge=0, le=100)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anomaly_window_minutes: int = Field(default=15, gt=0)
    anomaly_baseline_minutes: int = Field(default=180, gt=0)
    anomaly_min_transactions: int = Field(default=6, gt=0)

    anomaly_amount_tolerance_percent: float = Field(
        default=2.0,
        gt=0,
        le=20,
    )

    anomaly_medium_threshold: int = Field(
        default=40,
        ge=0,
        le=100,
    )

    anomaly_high_threshold: int = Field(
        default=70,
        ge=0,
        le=100,
    )

    @property
    def parsed_cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]

    def validate_thresholds(self) -> None:
        if self.liquidity_critical_minutes >= self.liquidity_watch_minutes:
            raise ValueError(
                "LIQUIDITY_CRITICAL_MINUTES must be lower than "
                "LIQUIDITY_WATCH_MINUTES."
            )

        if self.feed_stale_minutes >= self.feed_missing_minutes:
            raise ValueError(
                "FEED_STALE_MINUTES must be lower than "
                "FEED_MISSING_MINUTES."
            )

        if self.anomaly_medium_threshold >= self.anomaly_high_threshold:
            raise ValueError(
                "ANOMALY_MEDIUM_THRESHOLD must be lower than "
                "ANOMALY_HIGH_THRESHOLD."
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_thresholds()
    return settings