"""Circuit breaker para dependencias externas (modelos, microservicio de identidad)."""

from __future__ import annotations

import time
from collections.abc import Callable


class CircuitBreaker:
    """Se abre tras ``failure_threshold`` fallas consecutivas y deja pasar una prueba
    cuando vence ``cooldown_s`` (semiabierto). Un éxito lo cierra de nuevo."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        return self._clock() - self._opened_at < self._cooldown_s

    def allow(self) -> bool:
        return not self.is_open

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()
