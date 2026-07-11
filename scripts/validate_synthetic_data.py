from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT_DIRECTORY = Path(__file__).resolve().parents[1]

if str(ROOT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(ROOT_DIRECTORY))


from app.replay.loader import (  # noqa: E402
    SyntheticDataError,
    SyntheticDataLoader,
)


def main() -> int:
    loader = SyntheticDataLoader(
        ROOT_DIRECTORY / "data" / "synthetic"
    )

    try:
        bundle = loader.load()
        event_stream = loader.build_event_stream(bundle)
        summary = loader.summary(bundle)

    except SyntheticDataError as exc:
        print("Synthetic data validation failed.")
        print(exc)
        return 1

    print("Synthetic data validation successful.\n")

    print(
        json.dumps(
            summary,
            indent=2,
            default=str,
        )
    )

    print("\nFirst five replay events:")

    for event in event_stream[:5]:
        print(
            f"{event.timestamp.isoformat()} | "
            f"{event.event_type.value:<12} | "
            f"{event.event_id:<12} | "
            f"{event.agent_id:<6} | "
            f"{event.provider_id.value}"
        )

    print("\nLast five replay events:")

    for event in event_stream[-5:]:
        print(
            f"{event.timestamp.isoformat()} | "
            f"{event.event_type.value:<12} | "
            f"{event.event_id:<12} | "
            f"{event.agent_id:<6} | "
            f"{event.provider_id.value}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())