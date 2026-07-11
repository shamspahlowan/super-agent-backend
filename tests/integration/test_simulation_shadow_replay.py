from pathlib import Path

import pytest

from app.evaluation.shadow_replay import (
    ShadowReplayValidator,
)


ROOT_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def validation_report():
    validator = ShadowReplayValidator(
        synthetic_dir=(
            ROOT_DIR
            / "data"
            / "synthetic_v2"
        ),
        ground_truth_dir=(
            ROOT_DIR
            / "data"
            / "ground_truth_v2"
        ),
    )

    return validator.run()


def test_public_invariants_pass(
    validation_report,
) -> None:
    failures = [
        check
        for check
        in validation_report.public_invariants
        if check.status.value == "FAIL"
    ]

    assert failures == []


def test_all_expected_scenarios_are_validated(
    validation_report,
) -> None:
    results = {
        result.scenario_id: result
        for result
        in validation_report.scenario_results
    }

    assert {
        "S2",
        "S3",
        "S4",
        "S5",
        "S6",
        "S7",
    }.issubset(results)

    assert results["S2"].passed
    assert results["S3"].passed
    assert results["S4"].passed
    assert results["S5"].passed
    assert results["S6"].passed
    assert results["S7"].passed


def test_anomaly_metrics_pass(
    validation_report,
) -> None:
    metrics = {
        metric.name: metric
        for metric
        in validation_report.metrics
    }

    assert metrics[
        "anomaly_precision"
    ].passed

    assert metrics[
        "anomaly_recall"
    ].passed

    assert metrics[
        "hard_negative_false_positive_rate"
    ].passed


def test_incident_and_case_coverage_pass(
    validation_report,
) -> None:
    metrics = {
        metric.name: metric
        for metric
        in validation_report.metrics
    }

    assert metrics[
        "incident_explanation_coverage"
    ].passed

    assert metrics[
        "incident_case_coverage"
    ].passed