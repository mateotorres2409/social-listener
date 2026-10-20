"""Puerto de entrada común de los casos de uso que ejecutan una etapa del pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from social_listener.application.dto.pipeline import Job
from social_listener.application.ports.outbound.unit_of_work import UnitOfWork


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """``advance=False`` detiene la mención en esta etapa (p. ej. descartada)."""

    advance: bool = True


ADVANCE = StageOutcome()
STOP = StageOutcome(advance=False)


class PermanentStageError(Exception):
    """Error que no se resuelve reintentando: el trabajo va directo a la cola de errores."""


class StageHandler(Protocol):
    async def handle(self, uow: UnitOfWork, job: Job) -> StageOutcome:
        """Procesa el trabajo dentro de la transacción ``uow`` (no hace commit)."""
        ...
