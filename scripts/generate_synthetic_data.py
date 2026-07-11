from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SEED = 20260711
random.seed(SEED)

ROOT_DIR = Path(__file__).resolve().parents[1]
SYNTHETIC_DIR = ROOT_DIR / "data" / "synthetic"
GROUND_TRUTH_DIR = ROOT_DIR / "data" / "ground_truth"

SYNTHETIC_DIR.mkdir(parents=True, exist_ok=True)
GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)

DHAKA_TIME = timezone(timedelta(hours=6))
SIMULATION_START = datetime(2026, 6, 20, 9, 0, tzinfo=DHAKA_TIME)
SIMULATION_END = datetime(2026, 6, 20, 18, 0, tzinfo=DHAKA_TIME)

PROVIDERS = ["BKASH", "NAGAD", "ROCKET"]

AGENTS = [
    {
        "agent_id": "AG001",
        "agent_name": "Zindabazar Digital Point",
        "area": "Zindabazar",
        "district": "Sylhet",
    },
    {
        "agent_id": "AG002",
        "agent_name": "Amberkhana Agent Corner",
        "area": "Amberkhana",
        "district": "Sylhet",
    },
    {
        "agent_id": "AG003",
        "agent_name": "Mirabazar Mobile Banking",
        "area": "Mirabazar",
        "district": "Sylhet",
    },
    {
        "agent_id": "AG004",
        "agent_name": "Zindabazar Express",
        "area": "Zindabazar",
        "district": "Sylhet",
    },
    {
        "agent_id": "AG005",
        "agent_name": "Subidbazar Finance Point",
        "area": "Subidbazar",
        "district": "Sylhet",
    },
    {
        "agent_id": "AG006",
        "agent_name": "Bondor Bazar Agent",
        "area": "Bondor Bazar",
        "district": "Sylhet",
    },
]

OPENING_CASH = {
    "AG001": 300_000,
    "AG002": 250_000,
    "AG003": 110_000,
    "AG004": 350_000,
    "AG005": 220_000,
    "AG006": 280_000,
}

OPENING_EMONEY = {
    ("AG001", "BKASH"): 160_000,
    ("AG001", "NAGAD"): 170_000,
    ("AG001", "ROCKET"): 150_000,

    # Low bKash e-money supports hidden provider-shortage scenario.
    ("AG002", "BKASH"): 70_000,
    ("AG002", "NAGAD"): 190_000,
    ("AG002", "ROCKET"): 180_000,

    # Low shared cash supports combined liquidity/anomaly scenario.
    ("AG003", "BKASH"): 180_000,
    ("AG003", "NAGAD"): 220_000,
    ("AG003", "ROCKET"): 170_000,

    ("AG004", "BKASH"): 250_000,
    ("AG004", "NAGAD"): 240_000,
    ("AG004", "ROCKET"): 230_000,

    ("AG005", "BKASH"): 160_000,
    ("AG005", "NAGAD"): 160_000,
    ("AG005", "ROCKET"): 160_000,

    ("AG006", "BKASH"): 180_000,
    ("AG006", "NAGAD"): 190_000,
    ("AG006", "ROCKET"): 170_000,
}


transactions: list[dict[str, Any]] = []
feed_events: list[dict[str, Any]] = []
ground_truth: list[dict[str, Any]] = []

transaction_counter = 1
feed_event_counter = 1


def iso_time(value: datetime) -> str:
    return value.isoformat()


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def add_transaction(
    *,
    timestamp: datetime,
    agent_id: str,
    provider_id: str,
    account_id: str,
    transaction_type: str,
    amount: int,
    status: str = "SUCCESS",
    channel: str = "AGENT",
    scenario_id: str | None = None,
    expected_category: str | None = None,
    expected_positive: bool | None = None,
    details: str = "",
) -> str:
    global transaction_counter

    transaction_id = f"TXN-{transaction_counter:06d}"
    transaction_counter += 1

    # scenario_id is intentionally not stored in transactions.csv.
    transactions.append(
        {
            "transaction_id": transaction_id,
            "timestamp": iso_time(timestamp),
            "agent_id": agent_id,
            "provider_id": provider_id,
            "account_id": account_id,
            "transaction_type": transaction_type,
            "amount": amount,
            "status": status,
            "channel": channel,
        }
    )

    if scenario_id:
        ground_truth.append(
            {
                "record_type": "TRANSACTION",
                "record_id": transaction_id,
                "scenario_id": scenario_id,
                "expected_category": expected_category or "",
                "expected_positive": int(bool(expected_positive)),
                "details": details,
            }
        )

    return transaction_id


def add_feed_event(
    *,
    timestamp: datetime,
    agent_id: str,
    provider_id: str,
    event_type: str,
    delay_minutes: int = 0,
    reported_balance: int | None = None,
    scenario_id: str | None = None,
    expected_category: str | None = None,
    expected_positive: bool | None = None,
    details: str = "",
) -> str:
    global feed_event_counter

    event_id = f"FEED-{feed_event_counter:05d}"
    feed_event_counter += 1

    feed_events.append(
        {
            "feed_event_id": event_id,
            "timestamp": iso_time(timestamp),
            "agent_id": agent_id,
            "provider_id": provider_id,
            "event_type": event_type,
            "delay_minutes": delay_minutes,
            "reported_balance": (
                reported_balance if reported_balance is not None else ""
            ),
        }
    )

    if scenario_id:
        ground_truth.append(
            {
                "record_type": "FEED_EVENT",
                "record_id": event_id,
                "scenario_id": scenario_id,
                "expected_category": expected_category or "",
                "expected_positive": int(bool(expected_positive)),
                "details": details,
            }
        )

    return event_id


def generate_opening_balances() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for agent in AGENTS:
        agent_id = agent["agent_id"]

        rows.append(
            {
                "agent_id": agent_id,
                "provider_id": "",
                "resource_type": "SHARED_CASH",
                "opening_balance": OPENING_CASH[agent_id],
                "timestamp": iso_time(SIMULATION_START),
            }
        )

        for provider_id in PROVIDERS:
            rows.append(
                {
                    "agent_id": agent_id,
                    "provider_id": provider_id,
                    "resource_type": "PROVIDER_EMONEY",
                    "opening_balance": OPENING_EMONEY[
                        (agent_id, provider_id)
                    ],
                    "timestamp": iso_time(SIMULATION_START),
                }
            )

    return rows


def generate_normal_transactions() -> None:
    amounts = [
        500,
        800,
        1_000,
        1_200,
        1_500,
        2_000,
        2_500,
        3_000,
        4_000,
        5_000,
    ]

    for agent in AGENTS:
        agent_id = agent["agent_id"]

        for provider_id in PROVIDERS:
            timestamp = SIMULATION_START + timedelta(
                minutes=random.randint(2, 12)
            )

            for index in range(14):
                timestamp += timedelta(minutes=random.randint(18, 32))

                if timestamp >= SIMULATION_END:
                    break

                transaction_type = (
                    "CASH_OUT"
                    if random.random() < 0.54
                    else "CASH_IN"
                )

                status = (
                    "FAILED"
                    if random.random() < 0.025
                    else "SUCCESS"
                )

                add_transaction(
                    timestamp=timestamp,
                    agent_id=agent_id,
                    provider_id=provider_id,
                    account_id=(
                        f"ACC-{agent_id[-3:]}-"
                        f"{provider_id[:2]}-{index:03d}"
                    ),
                    transaction_type=transaction_type,
                    amount=random.choice(amounts),
                    status=status,
                )


def inject_hidden_provider_shortage() -> None:
    start = datetime(
        2026, 6, 20, 11, 30, tzinfo=DHAKA_TIME
    )

    # CASH_IN increases physical cash but decreases provider e-money.
    for index in range(12):
        add_transaction(
            timestamp=start + timedelta(minutes=index * 4),
            agent_id="AG002",
            provider_id="BKASH",
            account_id=f"ACC-S2-{index:03d}",
            transaction_type="CASH_IN",
            amount=random.randint(5_800, 7_000),
            scenario_id="S2",
            expected_category="PROVIDER_LIQUIDITY_PRESSURE",
            expected_positive=True,
            details=(
                "Repeated bKash cash-in demand should deplete "
                "provider e-money while shared physical cash remains healthy."
            ),
        )


def inject_combined_liquidity_and_anomaly() -> None:
    start = datetime(
        2026, 6, 20, 14, 0, tzinfo=DHAKA_TIME
    )

    repeated_accounts = [
        "ACC-S3-A",
        "ACC-S3-B",
        "ACC-S3-C",
    ]

    repeated_amounts = [
        9_900,
        10_000,
        10_050,
        9_950,
        10_000,
        10_100,
        9_980,
        10_020,
        10_000,
    ]

    for index, amount in enumerate(repeated_amounts):
        add_transaction(
            timestamp=start + timedelta(minutes=index * 2),
            agent_id="AG003",
            provider_id="NAGAD",
            account_id=repeated_accounts[index % 3],
            transaction_type="CASH_OUT",
            amount=amount,
            scenario_id="S3",
            expected_category="REQUIRES_REVIEW",
            expected_positive=True,
            details=(
                "Near-identical cash-out amounts from a small group "
                "of repeating accounts while shared cash is falling."
            ),
        )


def inject_legitimate_eid_demand_spike() -> None:
    start = datetime(
        2026, 6, 20, 15, 0, tzinfo=DHAKA_TIME
    )

    varied_amounts = [
        1_200,
        2_100,
        3_700,
        5_500,
        7_200,
        2_800,
        9_400,
        4_600,
        6_300,
        1_700,
        8_100,
        3_300,
        11_000,
        2_400,
        5_900,
        7_700,
        4_100,
        6_800,
    ]

    for index, amount in enumerate(varied_amounts):
        provider_id = PROVIDERS[index % len(PROVIDERS)]

        add_transaction(
            timestamp=start + timedelta(minutes=index * 2),
            agent_id="AG004",
            provider_id=provider_id,
            account_id=f"ACC-EID-{index:03d}",
            transaction_type="CASH_OUT",
            amount=amount,
            scenario_id="S5",
            expected_category="LEGITIMATE_DEMAND_SPIKE",
            expected_positive=False,
            details=(
                "Broad Eid demand involving many accounts, providers "
                "and varied amounts. It should not be treated as fraud."
            ),
        )


def inject_cross_provider_pattern() -> None:
    start = datetime(
        2026, 6, 20, 16, 10, tzinfo=DHAKA_TIME
    )

    linked_account = "SYNTH-LINK-9001"

    pattern = [
        ("BKASH", 14_800),
        ("NAGAD", 15_000),
        ("BKASH", 14_950),
        ("NAGAD", 15_100),
        ("BKASH", 15_000),
        ("NAGAD", 14_900),
    ]

    for index, (provider_id, amount) in enumerate(pattern):
        add_transaction(
            timestamp=start + timedelta(minutes=index * 2),
            agent_id="AG001",
            provider_id=provider_id,
            account_id=linked_account,
            transaction_type="CASH_OUT",
            amount=amount,
            scenario_id="S7",
            expected_category="CROSS_PROVIDER_REVIEW",
            expected_positive=True,
            details=(
                "Related synthetic identifier shows similar high-value "
                "activity across two logically separate providers."
            ),
        )


def generate_feed_events() -> None:
    for agent in AGENTS:
        for provider_id in PROVIDERS:
            timestamp = SIMULATION_START

            while timestamp <= SIMULATION_END:
                # Create an actual heartbeat gap for AG005/ROCKET.
                is_delayed_window = (
                    agent["agent_id"] == "AG005"
                    and provider_id == "ROCKET"
                    and datetime(
                        2026, 6, 20, 13, 0, tzinfo=DHAKA_TIME
                    )
                    <= timestamp
                    < datetime(
                        2026, 6, 20, 13, 40, tzinfo=DHAKA_TIME
                    )
                )

                if not is_delayed_window:
                    add_feed_event(
                        timestamp=timestamp,
                        agent_id=agent["agent_id"],
                        provider_id=provider_id,
                        event_type="HEARTBEAT",
                    )

                timestamp += timedelta(minutes=20)

    add_feed_event(
        timestamp=datetime(
            2026, 6, 20, 13, 0, tzinfo=DHAKA_TIME
        ),
        agent_id="AG005",
        provider_id="ROCKET",
        event_type="FEED_DELAY",
        delay_minutes=35,
        scenario_id="S4",
        expected_category="DATA_QUALITY_ISSUE",
        expected_positive=True,
        details=(
            "Rocket feed becomes stale. The system should reduce "
            "confidence and avoid strong recommendations."
        ),
    )

    add_feed_event(
        timestamp=datetime(
            2026, 6, 20, 13, 40, tzinfo=DHAKA_TIME
        ),
        agent_id="AG005",
        provider_id="ROCKET",
        event_type="FEED_RECOVERED",
        scenario_id="S4",
        expected_category="DATA_QUALITY_RECOVERY",
        expected_positive=True,
        details="Provider feed becomes available again.",
    )

    add_feed_event(
        timestamp=datetime(
            2026, 6, 20, 16, 0, tzinfo=DHAKA_TIME
        ),
        agent_id="AG006",
        provider_id="BKASH",
        event_type="BALANCE_CONFLICT",
        reported_balance=82_500,
        scenario_id="S6",
        expected_category="DATA_QUALITY_ISSUE",
        expected_positive=True,
        details=(
            "Reported provider balance conflicts with the balance "
            "calculated from opening balance and transaction deltas."
        ),
    )


def generate_context_events() -> list[dict[str, Any]]:
    return [
        {
            "context_id": "CTX-001",
            "area": "Zindabazar",
            "event_type": "EID_MARKET_SURGE",
            "start_time": iso_time(
                datetime(
                    2026, 6, 20, 14, 45, tzinfo=DHAKA_TIME
                )
            ),
            "end_time": iso_time(
                datetime(
                    2026, 6, 20, 16, 0, tzinfo=DHAKA_TIME
                )
            ),
            "expected_demand_multiplier": 2.5,
            "description": (
                "Expected pre-Eid demand increase involving "
                "many unrelated customers."
            ),
        }
    ]


def generate_scenario_manifest() -> list[dict[str, Any]]:
    return [
        {
            "scenario_id": "S2",
            "name": "Hidden provider shortage",
            "agent_id": "AG002",
            "provider_id": "BKASH",
            "expected_outcome": "provider_liquidity_pressure",
        },
        {
            "scenario_id": "S3",
            "name": "Liquidity pressure with unusual activity",
            "agent_id": "AG003",
            "provider_id": "NAGAD",
            "expected_outcome": "combined_priority_case",
        },
        {
            "scenario_id": "S4",
            "name": "Delayed provider feed",
            "agent_id": "AG005",
            "provider_id": "ROCKET",
            "expected_outcome": "insufficient_data",
        },
        {
            "scenario_id": "S5",
            "name": "Legitimate Eid demand spike",
            "agent_id": "AG004",
            "provider_id": "MULTIPLE",
            "expected_outcome": "demand_spike_not_fraud",
        },
        {
            "scenario_id": "S6",
            "name": "Conflicting provider balance",
            "agent_id": "AG006",
            "provider_id": "BKASH",
            "expected_outcome": "data_quality_issue",
        },
        {
            "scenario_id": "S7",
            "name": "Privacy-safe cross-provider pattern",
            "agent_id": "AG001",
            "provider_id": "BKASH,NAGAD",
            "expected_outcome": "requires_human_review",
        },
    ]


def main() -> None:
    generate_normal_transactions()
    inject_hidden_provider_shortage()
    inject_combined_liquidity_and_anomaly()
    inject_legitimate_eid_demand_spike()
    inject_cross_provider_pattern()
    generate_feed_events()

    transactions.sort(key=lambda row: row["timestamp"])
    feed_events.sort(key=lambda row: row["timestamp"])

    write_csv(
        SYNTHETIC_DIR / "agents.csv",
        AGENTS,
        ["agent_id", "agent_name", "area", "district"],
    )

    write_csv(
        SYNTHETIC_DIR / "opening_balances.csv",
        generate_opening_balances(),
        [
            "agent_id",
            "provider_id",
            "resource_type",
            "opening_balance",
            "timestamp",
        ],
    )

    write_csv(
        SYNTHETIC_DIR / "transactions.csv",
        transactions,
        [
            "transaction_id",
            "timestamp",
            "agent_id",
            "provider_id",
            "account_id",
            "transaction_type",
            "amount",
            "status",
            "channel",
        ],
    )

    write_csv(
        SYNTHETIC_DIR / "feed_events.csv",
        feed_events,
        [
            "feed_event_id",
            "timestamp",
            "agent_id",
            "provider_id",
            "event_type",
            "delay_minutes",
            "reported_balance",
        ],
    )

    write_csv(
        SYNTHETIC_DIR / "context_events.csv",
        generate_context_events(),
        [
            "context_id",
            "area",
            "event_type",
            "start_time",
            "end_time",
            "expected_demand_multiplier",
            "description",
        ],
    )

    write_csv(
        GROUND_TRUTH_DIR / "scenario_labels.csv",
        ground_truth,
        [
            "record_type",
            "record_id",
            "scenario_id",
            "expected_category",
            "expected_positive",
            "details",
        ],
    )

    manifest_path = GROUND_TRUTH_DIR / "scenario_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "seed": SEED,
                "simulation_start": iso_time(SIMULATION_START),
                "simulation_end": iso_time(SIMULATION_END),
                "scenarios": generate_scenario_manifest(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print("Synthetic data generated successfully.")
    print(f"Transactions: {len(transactions)}")
    print(f"Feed events: {len(feed_events)}")
    print(f"Ground-truth labels: {len(ground_truth)}")
    print(f"Location: {SYNTHETIC_DIR}")


if __name__ == "__main__":
    main()