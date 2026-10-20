"""Backoff exponencial con jitter."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class Backoff:
    base_s: float = 2.0
    max_s: float = 600.0
    jitter: float = 0.25

    def delay(self, attempts: int, rng: Callable[[], float] = random.random) -> timedelta:
        raw = min(self.max_s, self.base_s * (2**attempts))
        spread = raw * self.jitter
        return timedelta(seconds=max(0.0, raw - spread + 2 * spread * rng()))
