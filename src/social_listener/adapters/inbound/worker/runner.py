"""Runner de workers: toma trabajos de una etapa y ejecuta su caso de uso.

Cada trabajo se procesa en su propia transacción: el resultado de la etapa, el
encolado de la siguiente y el cierre del trabajo se confirman juntos. Si la etapa
falla, se descarta todo y el fallo se registra en una transacción aparte.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from typing import Protocol

from social_listener.application.backoff import Backoff
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.pipeline import next_stage
from social_listener.application.ports.inbound.stages import PermanentStageError, StageHandler
from social_listener.application.ports.outbound.services import Clock
from social_listener.application.ports.outbound.unit_of_work import UnitOfWorkFactory

logger = logging.getLogger(__name__)


class Instrumentation(Protocol):
    """Puntos de instrumentación del runner (trazas y métricas)."""

    def job_span(self, job: Job) -> AbstractAsyncContextManager[None]: ...
    def record_success(self, stage: Stage, seconds: float) -> None: ...
    def record_failure(self, stage: Stage, seconds: float, *, dead_letter: bool) -> None: ...


class NoInstrumentation:
    @asynccontextmanager
    async def job_span(self, job: Job) -> AsyncIterator[None]:
        yield

    def record_success(self, stage: Stage, seconds: float) -> None:
        return None

    def record_failure(self, stage: Stage, seconds: float, *, dead_letter: bool) -> None:
        return None


class StageRunner:
    def __init__(
        self,
        stage: Stage,
        handler: StageHandler,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        *,
        backoff: Backoff | None = None,
        instrumentation: Instrumentation | None = None,
        idle_sleep_s: float = 1.0,
    ) -> None:
        self.stage = stage
        self._handler = handler
        self._uow_factory = uow_factory
        self._clock = clock
        self._backoff = backoff or Backoff()
        self._instrumentation = instrumentation or NoInstrumentation()
        self._idle_sleep_s = idle_sleep_s

    async def run_once(self) -> bool:
        """Procesa a lo sumo un trabajo. Devuelve False si no había trabajo."""
        loop = asyncio.get_running_loop()
        async with self._uow_factory() as uow:
            jobs = await uow.jobs.claim(self.stage, 1)
            if not jobs:
                return False
            job = jobs[0]
            started = loop.time()
            try:
                async with self._instrumentation.job_span(job):
                    outcome = await self._handler.handle(uow, job)
                    following = next_stage(self.stage)
                    if outcome.advance and following is not None:
                        await uow.jobs.enqueue(following, job.subject_id)
                    await uow.jobs.complete(job)
                    await uow.commit()
            except Exception as exc:
                await uow.rollback()
                error = exc
            else:
                self._instrumentation.record_success(self.stage, loop.time() - started)
                return True

        dead = await self._register_failure(job, error)
        self._instrumentation.record_failure(self.stage, loop.time() - started, dead_letter=dead)
        return True

    async def _register_failure(self, job: Job, error: Exception) -> bool:
        reason = f"{type(error).__name__}: {error}"
        retry_at = (
            None
            if isinstance(error, PermanentStageError)
            else self._clock.now() + self._backoff.delay(job.attempts)
        )
        async with self._uow_factory() as uow:
            dead = await uow.jobs.fail(job, reason, retry_at=retry_at)
            await uow.commit()
        logger.warning(
            "falló la etapa %s del sujeto %s (intento %d): %s",
            self.stage.value,
            job.subject_id,
            job.attempts + 1,
            type(error).__name__,
            extra={"dead_letter": dead},
        )
        return dead

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            worked = await self.run_once()
            if not worked:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self._idle_sleep_s)
