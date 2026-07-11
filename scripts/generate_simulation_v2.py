from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIRECTORY = Path(__file__).resolve().parents[1]

if str(ROOT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(ROOT_DIRECTORY))

from app.simulation.config import (
    load_simulation_config,
)
from app.simulation.exporter import (
    SimulationExporter,
)
from app.simulation.generator import (
    BaselineSimulationGenerator,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate deterministic stateful synthetic "
            "multi-provider agent activity."
        )
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/simulations/demo_day.yaml"
        ),
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

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    config = load_simulation_config(
        args.config
    )

    generator = BaselineSimulationGenerator(
        config
    )

    run = generator.generate()

    exporter = SimulationExporter(
        synthetic_dir=args.synthetic_dir,
        ground_truth_dir=(
            args.ground_truth_dir
        ),
    )

    exporter.export(run)

    print("Simulation generated successfully")
    print(f"Run ID: {run.run_id}")
    print(f"Seed: {run.seed}")
    print(
        f"Transactions: {len(run.transactions)}"
    )
    print(
        "Synthetic data: "
        f"{args.synthetic_dir.resolve()}"
    )
    print(
        "Ground truth: "
        f"{args.ground_truth_dir.resolve()}"
    )
    print(
        "Status counts: "
        f"{run.manifest['status_counts']}"
    )
    print(
        "Failure reasons: "
        f"{run.manifest['failure_reason_counts']}"
    )


if __name__ == "__main__":
    main()