from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from social_listener.adapters.inbound.worker.runner import StageRunner
from social_listener.application.backoff import Backoff
from social_listener.application.dto.pipeline import Job, Stage
from social_listener.application.pipeline import TRANSITIONS
from social_listener.application.ports.inbound.stages import (
    ADVANCE,
    STOP,
    PermanentStageError,
    StageOutcome,
)
from social_listener.application.ports.outbound.pipeline import AuditEntry
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork
from tests.contracts.persistence import T0
from tests.fakes.memory import FixedClock, InMemoryDatabase


@dataclass
class Handler:
    outcome: StageOutcome = ADVANCE
    error: Exception | None = None
    seen: list[str] = field(default_factory=list)

    async def handle(self, uow: UnitOfWork, job: Job) -> StageOutcome:
        self.seen.append(job.subject_id)
        await uow.audit.record(AuditEntry("etapa", job.subject_id, T0, "prueba"))
        if self.error is not None:
            raise self.error
        return self.outcome


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0)


@pytest.fixture
def db(clock: FixedClock) -> InMemoryDatabase:
    return InMemoryDatabase(clock, max_attempts=3)


async def _enqueue(db: InMemoryDatabase, stage: Stage, subject: str) -> None:
    async with db() as uow:
        await uow.jobs.enqueue(stage, subject)
        await uow.commit()


def _pending(db: InMemoryDatabase, stage: Stage) -> list[str]:
    return [
        s.job.subject_id
        for s in db.state.jobs.values()
        if s.job.stage == stage and s.status == "pendiente"
    ]


def test_transiciones_cubren_todas_las_etapas_por_mencion() -> None:
    assert TRANSITIONS[Stage.PROTECT] is Stage.CLASSIFY
    assert TRANSITIONS[Stage.IDENTITY] is Stage.EXPORT
    assert TRANSITIONS[Stage.EXPORT] is None
    assert set(TRANSITIONS) == set(Stage) - {Stage.NORMALIZE}


async def test_etapa_exitosa_encola_la_siguiente(db: InMemoryDatabase, clock: FixedClock) -> None:
    await _enqueue(db, Stage.CLASSIFY, "m-1")
    runner = StageRunner(Stage.CLASSIFY, Handler(), db, clock)

    assert await runner.run_once()

    assert _pending(db, Stage.INDIVIDUALIZE) == ["m-1"]
    assert _pending(db, Stage.CLASSIFY) == []
    assert len(db.state.audit) == 1
    assert not await runner.run_once()


async def test_etapa_fallida_no_encola_la_siguiente_y_reintenta(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    await _enqueue(db, Stage.CLASSIFY, "m-1")
    handler = Handler(error=RuntimeError("se cayó"))
    runner = StageRunner(Stage.CLASSIFY, handler, db, clock, backoff=Backoff(base_s=10))

    assert await runner.run_once()

    assert _pending(db, Stage.INDIVIDUALIZE) == []
    assert db.state.audit == []  # lo escrito por la etapa fallida se descartó
    (stored,) = db.state.jobs.values()
    assert stored.job.attempts == 1
    assert stored.available_at > clock.now()
    assert not await runner.run_once()  # aún no vence el backoff


async def test_reintentos_agotados_van_a_la_cola_de_errores(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    await _enqueue(db, Stage.CLASSIFY, "m-1")
    runner = StageRunner(
        Stage.CLASSIFY, Handler(error=RuntimeError("x")), db, clock, backoff=Backoff(base_s=0)
    )

    for _ in range(3):
        assert await runner.run_once()

    assert db.state.jobs == {}
    (letter,) = db.state.dead_letters.values()
    assert letter.stage == Stage.CLASSIFY
    assert letter.attempts == 3
    assert "RuntimeError" in letter.reason


async def test_error_permanente_va_directo_a_la_cola_de_errores(
    db: InMemoryDatabase, clock: FixedClock
) -> None:
    await _enqueue(db, Stage.PROTECT, "m-1")
    runner = StageRunner(Stage.PROTECT, Handler(error=PermanentStageError("inválida")), db, clock)

    await runner.run_once()

    assert len(db.state.dead_letters) == 1


async def test_resultado_stop_no_avanza(db: InMemoryDatabase, clock: FixedClock) -> None:
    await _enqueue(db, Stage.CLASSIFY, "m-1")
    runner = StageRunner(Stage.CLASSIFY, Handler(outcome=STOP), db, clock)

    await runner.run_once()

    assert _pending(db, Stage.INDIVIDUALIZE) == []
    assert db.state.jobs == {} or all(s.status == "terminado" for s in db.state.jobs.values())


def test_backoff_exponencial_con_tope() -> None:
    backoff = Backoff(base_s=2, max_s=60, jitter=0)
    assert backoff.delay(0).total_seconds() == 2
    assert backoff.delay(3).total_seconds() == 16
    assert backoff.delay(10).total_seconds() == 60
