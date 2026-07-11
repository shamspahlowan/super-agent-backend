from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIRECTORY = Path(__file__).resolve().parents[1]

if str(ROOT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(ROOT_DIRECTORY))

from app.evaluation.shadow_replay import (
    ShadowReplayValidator,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate synthetic_v2 through the full "
            "decision-support pipeline."
        )
    )

    parser.add_argument(
        "--synthetic-dir",
        type=Path,
        default=Path(
            "data/synthetic_v2"
        ),
    )

    parser.add_argument(
        "--ground-truth-dir",
        type=Path,
        default=Path(
            "data/ground_truth_v2"
        ),
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            "artifacts/validation/"
            "simulation_v2_report.json"
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    validator = ShadowReplayValidator(
        synthetic_dir=args.synthetic_dir,
        ground_truth_dir=(
            args.ground_truth_dir
        ),
    )

    report = validator.run()

    args.report.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with args.report.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            report.model_dump(
                mode="json"
            ),
            file,
            indent=2,
            ensure_ascii=False,
        )

        file.write("\n")

    print()
    print("Simulation V2 validation")
    print("=" * 60)
    print(f"Run ID: {report.run_id}")
    print(f"Seed: {report.seed}")
    print(
        f"Replay events: "
        f"{report.total_replay_events}"
    )
    print(
        f"Replay duration: "
        f"{report.replay_duration_seconds}s"
    )
    print()

    print("Public invariants")
    print("-" * 60)

    for check in report.public_invariants:
        print(
            f"[{check.status.value}] "
            f"{check.name}: {check.observed}"
        )

    print()
    print("Scenario validation")
    print("-" * 60)

    for result in report.scenario_results:
        detected = (
            result.first_detected_at.isoformat()
            if result.first_detected_at
            else "not detected"
        )

        print(
            f"[{result.status.value}] "
            f"{result.scenario_id} "
            f"{result.label}"
        )

        print(
            f"  first detected: {detected}"
        )

        print(
            "  detection delay: "
            f"{result.detection_delay_minutes}"
        )

        for check in result.checks:
            print(
                f"  - [{check.status.value}] "
                f"{check.name}"
            )

    print()
    print("Metrics")
    print("-" * 60)

    for metric in report.metrics:
        print(
            f"[{metric.status.value}] "
            f"{metric.name}: "
            f"{metric.value} {metric.unit} "
            f"(target {metric.target})"
        )

    print()
    print(
        f"Overall: "
        f"{report.overall_status.value}"
    )

    print(
        f"Report: {args.report.resolve()}"
    )

    if not report.overall_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()