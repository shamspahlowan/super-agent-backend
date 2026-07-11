from __future__ import annotations

import hashlib
import random
from collections.abc import Sequence
from typing import TypeVar


T = TypeVar("T")


class SeedManager:
    """
    Creates deterministic and isolated random streams.

    Adding randomness to one component will not silently alter
    another component's random sequence.
    """

    def __init__(self, base_seed: int) -> None:
        self.base_seed = base_seed

        self._streams: dict[
            str,
            random.Random,
        ] = {}

    def child_seed(
        self,
        namespace: str,
    ) -> int:
        payload = (
            f"{self.base_seed}:{namespace}"
        ).encode("utf-8")

        digest = hashlib.sha256(payload).digest()

        return int.from_bytes(
            digest[:8],
            byteorder="big",
            signed=False,
        )

    def stream(
        self,
        namespace: str,
    ) -> random.Random:
        if namespace not in self._streams:
            self._streams[namespace] = random.Random(
                self.child_seed(namespace)
            )

        return self._streams[namespace]

    def weighted_choice(
        self,
        *,
        namespace: str,
        values: Sequence[T],
        weights: Sequence[float],
    ) -> T:
        if not values:
            raise ValueError(
                "weighted_choice requires at least one value."
            )

        if len(values) != len(weights):
            raise ValueError(
                "values and weights must have equal length."
            )

        if any(weight < 0 for weight in weights):
            raise ValueError(
                "Weights cannot be negative."
            )

        total_weight = sum(weights)

        if total_weight <= 0:
            raise ValueError(
                "Total weight must be greater than zero."
            )

        rng = self.stream(namespace)

        target = rng.random() * total_weight

        cumulative = 0.0

        for value, weight in zip(
            values,
            weights,
            strict=True,
        ):
            cumulative += weight

            if target <= cumulative:
                return value

        return values[-1]